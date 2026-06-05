#!/usr/bin/env node
/**
 * 通达信涨停板块 + 龙虎榜前20 自动更新脚本（完整版）
 * 数据源：同花顺 dataapi（涨停）+ ClawHub lhb-api（龙虎榜）
 * 用法：node tdx_zt_update.js [YYYYMMDD]
 *
 * 板块定义：
 *  - 首板 (SB.blk) = 1连板
 *  - 2连板 (2LB.blk) = 2连板
 *  - 3连板 (3LB.blk) = 3连板
 *  - 4连板 (4LB.blk) = 4连板
 *  - 5板以上 (5BYS.blk) = 5+连板
 *  - 所有连板 (SYLB.blk) = 2+连板
 *  - 昨封板 (ZFB.blk) = 目标日期涨停股
 *  - 龙虎榜前20 (LHBQ20.blk) = 龙虎榜净买入额前20
 */

const https = require('https');
const http = require('http');
const fs = require('fs');
const path = require('path');

// 读取配置：优先命令行参数，其次 config.json，最后默认值
function getConfig() {
    // 命令行参数：argv[3] = TDX目录
    if (process.argv[3]) {
        return process.argv[3];
    }
    // 读取同目录下的 config.json
    const configFile = path.join(path.dirname(process.argv[1]), 'config.json');
    if (fs.existsSync(configFile)) {
        try {
            const cfg = JSON.parse(fs.readFileSync(configFile, 'utf8'));
            if (cfg.blocknewDir) return cfg.blocknewDir;
        } catch (e) {}
    }
    // 默认值
    return 'C:/new_tdx/T0002/blocknew';
}

const TDX_DIR = getConfig();
const HISTORY_DAYS = 10; // 往前追溯10个交易日

// 获取上一个交易日
function getPrevTradingDay(dateStr) {
    const y = dateStr.slice(0, 4);
    const m = dateStr.slice(4, 6);
    const d = dateStr.slice(6, 8);
    const date = new Date(y + '-' + m + '-' + d);
    date.setDate(date.getDate() - 1);
    let day = date.getDay();
    if (day === 0) date.setDate(date.getDate() - 2);
    else if (day === 6) date.setDate(date.getDate() - 1);
    const mm = String(date.getMonth() + 1).padStart(2, '0');
    const dd = String(date.getDate()).padStart(2, '0');
    return date.getFullYear() + mm + dd;
}

// 获取目标日期（今天或指定日期）
function getTargetDate(dateArg) {
    if (dateArg) {
        return dateArg; // 指定日期模式：直接使用
    } else {
        let d = new Date();
        let day = d.getDay();
        if (day === 0) d.setDate(d.getDate() - 2);
        else if (day === 6) d.setDate(d.getDate() - 1);
        const hour = d.getHours();
        if (hour < 15) {
            d.setDate(d.getDate() - 1);
            day = d.getDay();
            if (day === 0) d.setDate(d.getDate() - 2);
            else if (day === 6) d.setDate(d.getDate() - 1);
        }
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const dd = String(d.getDate()).padStart(2, '0');
        return d.getFullYear() + mm + dd;
    }
}

// 获取交易日列表（从目标日期往前N天）
function getTradingDays(targetDate, days) {
    const dates = [targetDate];
    let prev = getPrevTradingDay(targetDate);
    for (let i = 0; i < days; i++) {
        dates.push(prev);
        prev = getPrevTradingDay(prev);
    }
    return dates;
}

// 从同花顺API获取涨停数据
function fetchLimitUpData(date) {
    return new Promise((resolve) => {
        const url = 'https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?page=1&limit=200&field=199112,10,9001,330323,330324,330325,133971,9002,133970,1968584,3475914,9003&order_field=330324&order_type=0&date=' + date + '&_=' + Date.now();
        const req = https.get(url, {
            headers: {
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'https://www.10jqka.com.cn/'
            }
        }, (res) => {
            let data = '';
            res.on('data', (chunk) => data += chunk);
            res.on('end', () => {
                try {
                    const json = JSON.parse(data);
                    if (json.status_code === 0 && json.data && json.data.info) {
                        resolve(json.data.info);
                    } else {
                        resolve([]);
                    }
                } catch (e) {
                    resolve([]);
                }
            });
        });
        req.on('error', () => resolve([]));
        req.setTimeout(10000, () => {
            req.destroy();
            resolve([]);
        });
    });
}

// 从 ClawHub LHB API 获取龙虎榜数据
function fetchLHBData(date) {
    return new Promise((resolve) => {
        // date 格式：YYYYMMDD → YYYY-MM-DD
        const fmt = date.slice(0, 4) + '-' + date.slice(4, 6) + '-' + date.slice(6, 8);
        const url = 'http://fffy520.gicp.net:8003/api/lhb/daily?date=' + fmt;
        const req = http.get(url, (res) => {
            if (res.statusCode !== 200) {
                resolve([]);
                return;
            }
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try {
                    const json = JSON.parse(data);
                    if (json.data && json.data.length > 0) {
                        resolve(json.data);
                    } else {
                        resolve([]);
                    }
                } catch (e) {
                    resolve([]);
                }
            });
        });
        req.on('error', () => resolve([]));
        req.setTimeout(10000, () => { req.destroy(); resolve([]); });
    });
}

// 计算连板天数
function calculateConsecutiveDays(targetDate, allData) {
    const targetStocks = allData[targetDate] || [];
    const consecutiveDays = {};

    for (const stock of targetStocks) {
        const code = stock.code;
        let days = 1;
        let checkDate = getPrevTradingDay(targetDate);

        while (allData[checkDate]) {
            const prevStocks = allData[checkDate];
            const found = prevStocks.find(s => s.code === code);
            if (found) {
                days++;
                checkDate = getPrevTradingDay(checkDate);
            } else {
                break;
            }
        }

        consecutiveDays[code] = days;
    }

    return consecutiveDays;
}

// 写入.blk文件
function writeBlkFile(filename, stocks) {
    const filepath = path.join(TDX_DIR, filename);
    const content = stocks.map(s => {
        const code = String(s.code).padStart(6, '0');
        let market = '0';
        if (code.startsWith('6')) market = '1';
        else if (code.startsWith('8') || code.startsWith('4')) market = '3';
        return market + code;
    }).join('\n') + '\n';
    fs.writeFileSync(filepath, content, 'utf8');
    console.log(`  ✓ ${filename}: ${stocks.length} 只`);
}

// 主函数
async function main() {
    const dateArg = process.argv[2];
    const targetDate = getTargetDate(dateArg);

    console.log('==================================================');
    console.log('通达信涨停板块 + 龙虎榜前20 自动更新脚本');
    console.log('==================================================');
    console.log(`目标日期: ${targetDate.slice(0,4)}-${targetDate.slice(4, 6)}-${targetDate.slice(6, 8)}`);
    console.log('');

    // 获取交易日列表
    console.log('【步骤1】获取交易日数据...');
    const tradingDays = getTradingDays(targetDate, HISTORY_DAYS);
    console.log(`  追溯 ${HISTORY_DAYS} 个交易日: ${tradingDays.slice(0, 6).join(', ')}...`);
    console.log('');

    // 获取所有交易日的数据
    console.log('【步骤2】获取涨停数据...');
    const allData = {};
    for (const date of tradingDays) {
        process.stdout.write(`  获取 ${date}... `);
        const stocks = await fetchLimitUpData(date);
        allData[date] = stocks;
        console.log(`${stocks.length} 只涨停`);
        await new Promise(resolve => setTimeout(resolve, 500));
    }
    console.log('');

    // 计算连板天数
    console.log('【步骤3】计算连板天数...');
    const consecutiveDays = calculateConsecutiveDays(targetDate, allData);
    const targetStocks = allData[targetDate] || [];
    console.log(`  ${targetDate} 涨停: ${targetStocks.length} 只`);
    console.log('');

    // 分类股票
    console.log('【步骤4】分类股票...');
    const categories = {
        shouban: [],    // 首板 (1连板)
        lianban2: [],    // 2连板
        lianban3: [],    // 3连板
        lianban4: [],    // 4连板
        lianban5: [],    // 5连板
        lianban5plus: [], // 6+连板
        allLianban: []   // 所有连板 (2+连板)
    };

    for (const stock of targetStocks) {
        const code = stock.code;
        const days = consecutiveDays[code] || 1;

        if (days === 1) {
            categories.shouban.push(stock);
        } else {
            categories.allLianban.push(stock);

            if (days === 2) categories.lianban2.push(stock);
            else if (days === 3) categories.lianban3.push(stock);
            else if (days === 4) categories.lianban4.push(stock);
            else if (days === 5) categories.lianban5.push(stock);
            else if (days >= 6) categories.lianban5plus.push(stock);
        }
    }

    console.log(`  首板: ${categories.shouban.length} 只`);
    console.log(`  2连板: ${categories.lianban2.length} 只`);
    console.log(`  3连板: ${categories.lianban3.length} 只`);
    console.log(`  4连板: ${categories.lianban4.length} 只`);
    console.log(`  5连板: ${categories.lianban5.length} 只`);
    console.log(`  6+连板: ${categories.lianban5plus.length} 只`);
    console.log(`  所有连板: ${categories.allLianban.length} 只`);
    console.log('');

    // 写入文件
    console.log('【步骤5】写入 .blk 文件...');
    writeBlkFile('SB.blk', categories.shouban);           // 首板
    writeBlkFile('SYLB.blk', categories.allLianban);     // 所有连板
    writeBlkFile('2LB.blk', categories.lianban2);       // 2连板
    writeBlkFile('3LB.blk', categories.lianban3);       // 3连板
    writeBlkFile('4LB.blk', categories.lianban4);       // 4连板

    // 5板以上 = 5连板 + 6+连板
    const lb5plus = [...categories.lianban5, ...categories.lianban5plus];
    writeBlkFile('5BYS.blk', lb5plus);                // 5板以上 (5+连板)
    writeBlkFile('ZFB.blk', targetStocks);                // 昨封板（目标日期涨停股）
    console.log('');

    // 龙虎榜前20
    console.log('【步骤6】龙虎榜前20...');
    const lhbData = await fetchLHBData(targetDate);
    if (lhbData && lhbData.length > 0) {
        // 按净买入额排序，取前20
        const top20 = [...lhbData].sort((a, b) => b.net_buy - a.net_buy).slice(0, 20);
        console.log(`  龙虎榜 ${lhbData.length} 只，取前20（按净买入额排序）:`);
        top20.forEach((s, i) => {
            console.log(`    ${i + 1}. ${s.code} ${s.name} 净买入:${s.net_buy}万 涨幅:${s.change}%`);
        });
        writeBlkFile('LHBQ20.blk', top20);
    } else {
        console.log('  龙虎榜数据获取失败');
    }
    console.log('');

    // 输出详细信息
    console.log('【复盘】各板块股票明细：');
    console.log('');

    if (categories.shouban.length > 0) {
        console.log(`首板(${categories.shouban.length}只):`);
        categories.shouban.forEach((s, i) => {
            console.log(`  ${i + 1}. ${s.code} ${s.name} 涨幅:${s.change_rate}%`);
        });
        console.log('');
    }

    if (categories.lianban2.length > 0) {
        console.log(`2连板(${categories.lianban2.length}只):`);
        categories.lianban2.forEach((s, i) => {
            console.log(`  ${i + 1}. ${s.code} ${s.name} 涨幅:${s.change_rate}%`);
        });
        console.log('');
    }

    if (categories.lianban3.length > 0) {
        console.log(`3连板(${categories.lianban3.length}只):`);
        categories.lianban3.forEach((s, i) => {
            console.log(`  ${i + 1}. ${s.code} ${s.name} 涨幅:${s.change_rate}%`);
        });
        console.log('');
    }

    if (categories.lianban4.length > 0) {
        console.log(`4连板(${categories.lianban4.length}只):`);
        categories.lianban4.forEach((s, i) => {
            console.log(`  ${i + 1}. ${s.code} ${s.name} 涨幅:${s.change_rate}%`);
        });
        console.log('');
    }

    if (categories.lianban5.length > 0) {
        console.log(`5连板(${categories.lianban5.length}只):`);
        categories.lianban5.forEach((s, i) => {
            console.log(`  ${i + 1}. ${s.code} ${s.name} 涨幅:${s.change_rate}%`);
        });
        console.log('');
    }

    if (categories.lianban5plus.length > 0) {
        console.log(`6+连板(${categories.lianban5plus.length}只):`);
        categories.lianban5plus.forEach((s, i) => {
            console.log(`  ${i + 1}. ${s.code} ${s.name} 涨幅:${s.change_rate}%`);
        });
        console.log('');
    }

    console.log('==================================================');
    console.log('✅ 完成！');
    console.log('==================================================');
}

main().catch(e => {
    console.error('错误:', e.message);
    process.exit(1);
});
