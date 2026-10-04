import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const sourceDir = path.resolve(process.argv[2]);
const outputDir = path.resolve(process.argv[3]);
const data = JSON.parse(await fs.readFile(path.join(sourceDir, 'economic_results.json'), 'utf8'));
const evidence = JSON.parse(await fs.readFile(path.join(sourceDir, 'parameters/lade_hangzhou_v2_evidence_20260930.json'), 'utf8'));
await fs.mkdir(outputDir, {recursive:true});
const wb = Workbook.create();
const overview = wb.worksheets.add('结果概览');
const rates = wb.worksheets.add('参数');
const costs = wb.worksheets.add('成本明细');
const pairs = wb.worksheets.add('单批比较');
const multi = wb.worksheets.add('多批比较');
const sensitivity = wb.worksheets.add('敏感性');
const dark = '#243D64', light = '#EEF2F7';
const money = '#,##0.00;(#,##0.00);"—"';
const integer = '#,##0;(#,##0);"—"';
const col = (i) => {let s='';for(i++;i>0;i=Math.floor((i-1)/26))s=String.fromCharCode(65+(i-1)%26)+s;return s;};
const ref = (sheet, c, r) => `'${sheet}'!${c}${r}`;
const rate = (r) => `'参数'!$B$${r}`;
function title(sheet, text, lastCol) {
  sheet.showGridLines=false;
  sheet.getRange(`A1:${lastCol}4`).format.font={name:'Arial',size:10,color:'#202D3D'};
  sheet.getRange('A2').values=[[text]];
  sheet.getRange('A2').format.font={name:'Arial',size:15,bold:true,color:dark};
  sheet.getRange(`A3:${lastCol}3`).format.borders={bottom:{style:'thin',color:'#B4C0D0'}};
}
function table(sheet, row, headers, n) {
  const end=col(headers.length-1), last=row+n;
  sheet.getRange(`A${row}:${end}${last}`).format.font={name:'Arial',size:10,color:'#202D3D'};
  sheet.getRange(`A${row}:${end}${last}`).format.verticalAlignment='center';
  sheet.getRange(`A${row}:${end}${last}`).format.rowHeight=22;
  sheet.getRange(`A${row}:${end}${row}`).values=[headers];
  sheet.getRange(`A${row}:${end}${row}`).format={fill:dark,font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},horizontalAlignment:'center',verticalAlignment:'center',wrapText:true,rowHeight:40};
  sheet.getRange(`A${row+1}:${end}${last}`).format.horizontalAlignment='right';
  sheet.getRange(`A${row+1}:A${last}`).format.horizontalAlignment='left';
  sheet.getRange(`A${row}:${end}${last}`).format.columnWidth=15;
  sheet.getRange(`A${row}:A${last}`).format.columnWidth=26;
  sheet.getRange(`A${row}:${end}${last}`).format.borders={bottom:{style:'thin',color:'#DCE3EC'}};
  sheet.freezePanes.freezeRows(row);
  sheet.freezePanes.freezeColumns(1);
}
function firstBreak(pure,reuse,investment,life,horizon) {
  return `IF(${pure}-${reuse}<=0,"未回本",IF(ROUNDUP(${investment}/(${pure}-${reuse}),0)>MIN(${life},${horizon}),"未回本",MAX(1,ROUNDUP(${investment}/(${pure}-${reuse}),0))))`;
}
title(overview,'LaDe 杭州经济性核算（2026-10-05）','I');
overview.tabColor=dark;
overview.getRange('A4').values=[['21 组案例：10/20/30 AOI 各 7 天。货量及位置来自 LaDe，路网来自保存的 OSRM 矩阵。']];
overview.getRange('A4').format.font={name:'Arial',size:10,italic:true,color:'#586579'};
title(rates,'费用参数与来源','E');
rates.tabColor='#496A96';
const sourceURL=(id)=>evidence.sources[id].url;
const rateRows=[
 ['一次性包装',4,'元/件','公开零售价参照',sourceURL('S1')],
 ['循环箱购置',36.5,'元/箱','2022 年企业采访历史价',sourceURL('S2')],
 ['容器寿命',50,'次使用','情景，B1 N=Q 时为运营周期',sourceURL('S2')],
 ['评价期',300,'运营周期','重复相同需求的情景',''],
 ['一级距离费',null,'元/km','燃油＋假设维保',''],
 ['二级距离费',null,'元/km','同一轻型柴油车代理',''],
 ['一级工时费',null,'元/min','年工资÷有效分钟×雇主乘数',''],
 ['二级工时费',null,'元/min','年工资÷有效分钟×雇主乘数',''],
 ['一级固定车辆费',100,'元/启用车周期','折旧保险分摊假设',''],
 ['二级固定车辆费',60,'元/启用车周期','折旧保险分摊假设',''],
 ['清洗',0.8,'元/归还箱次','分拣清洗人工已包含',''],
 ['跟踪',0.1,'元/发出箱次','非人工 IT/标签费用假设',''],
 ['一次性处置',0,'元/件','经营主体额外付费暂不计',''],
 ['柴油',8.45,'元/L','2026-05-21 浙江固定日期参考',sourceURL('S5')],
 ['油耗',8.9,'L/100km','2019 轻型货车研究基年代理',sourceURL('S4')],
 ['维保',0.15,'元/km','不含人工、折旧和保险的假设',''],
 ['一级人员年工资',103002,'元/年','杭州 2023 中级货运驾驶员中位数',sourceURL('S3')],
 ['二级人员年工资',70606,'元/年','杭州 2023 邮政快递人员中位数',sourceURL('S3')],
 ['有效工作时数',2000,'小时/年','250 天×8 小时情景假设',''],
 ['雇主成本乘数',1.25,'倍','情景，非规定费率',''],
];
table(rates,5,['参数','基准值','单位','依据','来源链接'],rateRows.length);
rates.getRange('A6:E25').values=rateRows;
rates.getRange('B6:B25').format.font={name:'Arial',size:10,color:'#0000FF'};
rates.getRange('B6:B25').setNumberFormat(money);
rates.getRange('B10').formulas=[[`=B19*B20/100+B21`]];
rates.getRange('B11').formulas=[['=B10']];
rates.getRange('B12').formulas=[['=B22/(B24*60)*B25']];
rates.getRange('B13').formulas=[['=B23/(B24*60)*B25']];
rates.getRange('B10:B13').setNumberFormat('0.00000000');
rates.getRange('B10:B13').format.font={name:'Arial',size:10,color:'#000000'};
rates.getRange('A5:A25').format.columnWidth=23;
rates.getRange('C5:C25').format.columnWidth=18;
rates.getRange('D5:D25').format.columnWidth=43;
rates.getRange('E5:E25').format.columnWidth=66;
rates.getRange('C6:E25').format.horizontalAlignment='left';
rates.getRange('E6:E25').format.wrapText=true;
rates.getRange('E6:E25').format.rowHeight=36;
rates.getRange('A28').values=[['基准价格来自不同年份，未平减。回收、寿命、清洗和车型仍为情景假设。']];
rates.getRange('A29').values=[['蓝色为输入。修改参数会更新成本与对照。敏感性固定路线，未重新优化路径。']];
rates.getRange('B8:B9').dataValidation={rule:{type:'whole',operator:'between',formula1:1,formula2:1000000}};

const cnyCosts=data.costs.filter(r=>r.dataset==='LaDe_CNY');
const lookup=new Map();
title(costs,'路线与成本组成（人民币）','Y');
table(costs,5,['案例','AOI数','批次数','模型','种子','可行状态','配送件数','箱池数量','一级km','二级km','一级min','二级min','一级车数','二级车数','距离费','工时费','车辆费','纸箱费','清洗费','跟踪费','运营费/周期','箱池初始投入','箱体使用分摊','分摊总费/周期','分摊元/件'],cnyCosts.length);
for(let i=0;i<cnyCosts.length;i++){
 const r=cnyCosts[i], n=i+6;
 lookup.set(`${r.family_id}|${r.seed}|${r.model}|${r.batch_count}`,n);
 costs.getRange(`A${n}:N${n}`).values=[[r.family_id,r.customer_count,r.batch_count,r.model,r.seed,r.valid?'有效':'缺少可行方案',r.packages,r.pool??null,r.e1_distance??null,r.e2_distance??null,r.e1_working_minutes??null,r.e2_working_minutes??null,r.e1_vehicle_count??null,r.e2_vehicle_count??null]];
 const f=[`I${n}*${rate(10)}+J${n}*${rate(11)}`,`K${n}*${rate(12)}+L${n}*${rate(13)}`,`M${n}*${rate(14)}+N${n}*${rate(15)}`,`IF(D${n}<=2,G${n}*(${rate(6)}+${rate(18)}),0)`,`IF(D${n}>=3,G${n}*${rate(16)},0)`,`IF(D${n}>=3,G${n}*${rate(17)},0)`,`SUM(O${n}:T${n})`,`H${n}*${rate(7)}`,`IF(D${n}>=3,G${n}*${rate(7)}/${rate(8)},0)`,`U${n}+W${n}`,`X${n}/G${n}`];
 costs.getRange(`O${n}:Y${n}`).formulas=[f.map(s=>`=IF(F${n}="有效",${s},"n.a.")`)];
}
costs.getRange(`B6:H${cnyCosts.length+5}`).setNumberFormat(integer);
costs.getRange(`I6:L${cnyCosts.length+5}`).setNumberFormat('0.00');
costs.getRange(`O6:Y${cnyCosts.length+5}`).setNumberFormat(money);
costs.getRange(`O6:Y${cnyCosts.length+5}`).format.font={name:'Arial',size:10,color:'#008000'};
costs.getRange(`F6:F${cnyCosts.length+5}`).format.columnWidth=19;
costs.getRange(`F6:F${cnyCosts.length+5}`).format.horizontalAlignment='left';

const cnyPairs=data.pairs.filter(r=>r.dataset==='LaDe_CNY');
title(pairs,'单批次一次性与循环包装对照','R');
table(pairs,5,['案例','AOI数','种子','包装对照','配对状态','一次性运营费','循环运营费','初始箱池投入','配送件数','寿命周期','评价周期','一次性累计费','循环累计费','累计节省','节省比例','一次性元/件','循环元/件','首次回本周期'],cnyPairs.length);
for(let i=0;i<cnyPairs.length;i++){
 const r=cnyPairs[i],n=i+6,pure=r.pair==='M1_vs_M3'?1:2,reuse=pure+2;
 const a=lookup.get(`${r.family_id}|${r.seed}|${pure}|1`),b=lookup.get(`${r.family_id}|${r.seed}|${reuse}|1`);
 pairs.getRange(`A${n}:E${n}`).values=[[r.family_id,r.customer_count,r.seed,r.pair.replace('_vs_',' / '),r.available?'有效':'缺少配对']];
 const expressions=[ref('成本明细','U',a),ref('成本明细','U',b),ref('成本明细','V',b),ref('成本明细','G',a),rate(8),rate(9),`F${n}*K${n}`,`G${n}*K${n}+H${n}*ROUNDUP(K${n}/J${n},0)`,`L${n}-M${n}`,`N${n}/L${n}`,`L${n}/(K${n}*I${n})`,`M${n}/(K${n}*I${n})`,firstBreak(`F${n}`,`G${n}`,`H${n}`,`J${n}`,`K${n}`)];
 pairs.getRange(`F${n}:R${n}`).formulas=[expressions.map(f=>`=IF(E${n}="有效",${f},"n.a.")`)];
}
pairs.getRange(`F6:N${cnyPairs.length+5}`).setNumberFormat(money);
pairs.getRange(`I6:K${cnyPairs.length+5}`).setNumberFormat(integer);
pairs.getRange(`O6:O${cnyPairs.length+5}`).setNumberFormat('0.0%');
pairs.getRange(`P6:Q${cnyPairs.length+5}`).setNumberFormat(money);
pairs.getRange(`D6:E${cnyPairs.length+5}`).format.horizontalAlignment='left';
pairs.getRange(`N6:N${cnyPairs.length+5}`).conditionalFormats.add('cellIs',{operator:'lessThan',formula:0,format:{fill:'#FCE8E6',font:{color:'#B32025'}}});
pairs.getRange(`D5:E${cnyPairs.length+5}`).format.columnWidth=18;

const cnyMulti=data.multibatch.filter(r=>r.dataset==='LaDe_CNY');
title(multi,'多批运营策略费用（人民币）','M');
multi.getRange('A4').values=[['B1 M1 与 B2/B3 M5 的策略对照。没有同批次数一次性控制组，不报告多批现金回本。']];
table(multi,5,['案例','AOI数','批次数','种子','配对状态','B1一次性费用','M5分摊总费','周期节省','节省比例','M5元/件','初始箱池投入','箱池数量','配送件数'],cnyMulti.length);
for(let i=0;i<cnyMulti.length;i++){
 const r=cnyMulti[i],n=i+6,a=lookup.get(`${r.family_id}|${r.seed}|1|1`),b=lookup.get(`${r.family_id}|${r.seed}|5|${r.batch_count}`);
 multi.getRange(`A${n}:E${n}`).values=[[r.family_id,r.customer_count,r.batch_count,r.seed,r.available?'有效':'缺少配对']];
 const expressions=[ref('成本明细','U',a),ref('成本明细','X',b),`F${n}-G${n}`,`H${n}/F${n}`,ref('成本明细','Y',b),ref('成本明细','V',b),ref('成本明细','H',b),ref('成本明细','G',b)];
 multi.getRange(`F${n}:M${n}`).formulas=[expressions.map(f=>`=IF(E${n}="有效",${f},"n.a.")`)];
}
multi.getRange(`F6:K${cnyMulti.length+5}`).setNumberFormat(money);
multi.getRange(`I6:I${cnyMulti.length+5}`).setNumberFormat('0.0%');
multi.getRange(`L6:M${cnyMulti.length+5}`).setNumberFormat(integer);
multi.getRange(`H6:H${cnyMulti.length+5}`).conditionalFormats.add('cellIs',{operator:'lessThan',formula:0,format:{fill:'#FCE8E6',font:{color:'#B32025'}}});

const summaries=data.summary.filter(r=>r.dataset==='LaDe_CNY');
table(overview,6,['AOI数','包装对照','计划配对','有效配对','累计不更贵','平均累计节省','合计节省率','一次性元/件','循环元/件'],summaries.length);
for(let i=0;i<summaries.length;i++){
 const r=summaries[i],n=i+7,end=cnyPairs.length+5,label=r.pair.replace('_vs_',' / ');
 overview.getRange(`A${n}:C${n}`).values=[[r.customer_count,label,r.planned_pairs]];
 const cond=`'单批比较'!$B$6:$B$${end},A${n},'单批比较'!$D$6:$D$${end},B${n},'单批比较'!$E$6:$E$${end},"有效"`;
 const sums=(c)=>`SUMIFS('单批比较'!$${c}$6:$${c}$${end},${cond})`;
 const expr=[`COUNTIFS(${cond})`,`COUNTIFS(${cond},'单批比较'!$N$6:$N$${end},">=0")`,`IF(D${n}=0,"n.a.",${sums('N')}/D${n})`,`IF(D${n}=0,"n.a.",${sums('N')}/${sums('L')})`,`IF(D${n}=0,"n.a.",${sums('L')}/(${sums('I')}*${rate(9)}))`,`IF(D${n}=0,"n.a.",${sums('M')}/(${sums('I')}*${rate(9)}))`];
 overview.getRange(`D${n}:I${n}`).formulas=[expr.map(f=>'='+f)];
}
overview.getRange('C7:E12').setNumberFormat(integer);
overview.getRange('F7:I12').setNumberFormat(money);
overview.getRange('G7:G12').setNumberFormat('0.0%');
overview.getRange('B6:B12').format.columnWidth=20;
overview.getRange('F6:F12').format.columnWidth=23;
overview.getRange('A14').values=[['单批为 300 个相同周期，循环费用包含每 50 周期的整池补购。正节省表示循环方案更便宜。']];
overview.getRange('A15').values=[['每组 3 个种子全部保留。需求子集嵌套、日期重复，不能把种子当作独立业务样本。']];
const mgroups=[10,20,30].flatMap(n=>[2,3].map(b=>[n,b]));
table(overview,18,['AOI数','批次数','计划配对','有效配对','分摊不更贵','平均周期节省','合计节省率','B1一次性元/件','M5分摊元/件'],mgroups.length);
for(let i=0;i<mgroups.length;i++){
 const n=i+19,[count,batch]=mgroups[i],end=cnyMulti.length+5;
 overview.getRange(`A${n}:C${n}`).values=[[count,batch,21]];
 const cond=`'多批比较'!$B$6:$B$${end},A${n},'多批比较'!$C$6:$C$${end},B${n},'多批比较'!$E$6:$E$${end},"有效"`;
 const sums=(c)=>`SUMIFS('多批比较'!$${c}$6:$${c}$${end},${cond})`;
 const expr=[`COUNTIFS(${cond})`,`COUNTIFS(${cond},'多批比较'!$H$6:$H$${end},">=0")`,`IF(D${n}=0,"n.a.",${sums('H')}/D${n})`,`IF(D${n}=0,"n.a.",${sums('H')}/${sums('F')})`,`IF(D${n}=0,"n.a.",${sums('F')}/${sums('M')})`,`IF(D${n}=0,"n.a.",${sums('G')}/${sums('M')})`];
 overview.getRange(`D${n}:I${n}`).formulas=[expr.map(f=>'='+f)];
}
overview.getRange('F19:I24').setNumberFormat(money);
overview.getRange('G19:G24').setNumberFormat('0.0%');
overview.getRange('A26').values=[['全部循环方案使用 N=Q。多批箱体损耗按实际箱次分摊，初始投入单列，不重复加入分摊总费。']];
overview.getRange('A27').values=[['费用依据为公开参考及情景假设，路线未按总费用优化。这些结果支持条件经济性判断。']];
overview.freezePanes.unfreeze();

title(sensitivity,'纸箱价格与寿命敏感性（固定路线）','L');
const grid=data.sensitivity.filter(r=>r.dataset==='LaDe_CNY');
table(sensitivity,5,['案例','AOI数','种子','包装对照','纸箱元/件','寿命周期','一次性运营费','循环运营费','初始箱池投入','评价周期','累计节省','首次回本周期'],grid.length);
for(let i=0;i<grid.length;i++){
 const r=grid[i],n=i+6,a=lookup.get(`${r.family_id}|${r.seed}|${r.single_use_model}|1`),b=lookup.get(`${r.family_id}|${r.seed}|${r.reusable_model}|1`);
 sensitivity.getRange(`A${n}:F${n}`).values=[[r.family_id,r.customer_count,r.seed,`M${r.single_use_model} / M${r.reusable_model}`,r.single_use_package_price,r.pool_lifetime_cycles]];
 sensitivity.getRange(`G${n}:L${n}`).formulas=[[
  `=${ref('成本明细','U',a)}+${ref('成本明细','G',a)}*(E${n}-${rate(6)})`,
  `=${ref('成本明细','U',b)}`,`=${ref('成本明细','V',b)}`,`=${rate(9)}`,
  `=J${n}*(G${n}-H${n})-I${n}*ROUNDUP(J${n}/F${n},0)`,
  `=${firstBreak(`G${n}`,`H${n}`,`I${n}`,`F${n}`,`J${n}`)}`]];
}
sensitivity.getRange(`E6:E${grid.length+5}`).setNumberFormat(money);
sensitivity.getRange(`G6:K${grid.length+5}`).setNumberFormat(money);
sensitivity.getRange(`J6:J${grid.length+5}`).setNumberFormat(integer);
sensitivity.getRange(`E6:F${grid.length+5}`).format.font={name:'Arial',size:10,color:'#0000FF'};
sensitivity.getRange(`D5:D${grid.length+5}`).format.columnWidth=20;
sensitivity.getRange(`K6:K${grid.length+5}`).conditionalFormats.add('cellIs',{operator:'lessThan',formula:0,format:{fill:'#FCE8E6',font:{color:'#B32025'}}});

wb.recalculate();
const checks=[];
function close(actual, expected, label) {
 if(typeof actual!=='number'||Math.abs(actual-expected)>1e-6*Math.max(1,Math.abs(expected)))throw new Error(`${label}: ${actual} != ${expected}`);
}
for(let i=0;i<cnyCosts.length;i++)if(cnyCosts[i].valid){
 close(costs.getRange(`U${i+6}`).values[0][0],cnyCosts[i].period_operating_cost,`operating ${i}`);
 close(costs.getRange(`X${i+6}`).values[0][0],cnyCosts[i].period_cost_with_use_depreciation,`allocation ${i}`);
}
for(let i=0;i<cnyPairs.length;i++)if(cnyPairs[i].available){
 close(pairs.getRange(`N${i+6}`).values[0][0],cnyPairs[i].horizon_saving,`cash saving ${i}`);
 const actual=pairs.getRange(`R${i+6}`).values[0][0],expected=cnyPairs[i].first_break_even_within_horizon??'未回本';
 if(actual!==expected)throw new Error(`break-even ${i}: ${actual} != ${expected}`);
}
for(let i=0;i<grid.length;i++)close(sensitivity.getRange(`K${i+6}`).values[0][0],grid[i].horizon_saving,`sensitivity ${i}`);
const testPair=cnyPairs.findIndex(r=>r.available),n=testPair+6;
const original=pairs.getRange(`N${n}`).values[0][0],q=pairs.getRange(`I${n}`).values[0][0];
rates.getRange('B6').values=[[5]];
close(pairs.getRange(`N${n}`).values[0][0]-original,300*q,'paper-price recalculation');
rates.getRange('B6').values=[[4]];
wb.recalculate();
close(pairs.getRange(`N${n}`).values[0][0],original,'restored base price');
const errorScan=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:50},maxChars:2500,summary:'Formula errors'});
await fs.writeFile(path.join(sourceDir,'workbook_formula_scan.ndjson'),errorScan.ndjson);
console.log(errorScan.ndjson);
const inspect=await wb.inspect({kind:'table',range:'结果概览!A6:I12',include:'values,formulas',tableMaxRows:7,tableMaxCols:9,maxChars:3000});
await fs.writeFile(path.join(sourceDir,'workbook_summary.inspect.ndjson'),inspect.ndjson);
for(const [name,range,suffix] of [['结果概览','A1:I28','overview'],['参数','A1:E25','parameters'],['成本明细','A1:N13','cost_inputs'],['成本明细','O5:Y13','cost_build'],['单批比较','A1:R13','single_batch'],['多批比较','A1:M13','multibatch'],['敏感性','A1:L13','sensitivity']]){
 const image=await wb.render({sheetName:name,range,scale:1.3,format:'png'});
 await fs.writeFile(path.join(sourceDir,`preview_${suffix}.png`),new Uint8Array(await image.arrayBuffer()));
}
const destination=path.join(outputDir,'经济可行性重新核算.xlsx');
const xlsx=await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(destination);
await fs.writeFile(path.join(sourceDir,'workbook_verification.json'),JSON.stringify({created_at:new Date().toISOString(),output:destination,cost_rows_checked:cnyCosts.length,pairs_checked:cnyPairs.length,sensitivity_rows_checked:grid.length,paper_price_input_change_verified:true,base_restored:true,engine:'Artifact Tool; Excel native UI not tested',sheets:6},null,2));
console.log(JSON.stringify({output:destination,cost_rows:cnyCosts.length,pairs:cnyPairs.length,sensitivity:grid.length}));
