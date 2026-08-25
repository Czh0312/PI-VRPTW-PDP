import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const projectRoot = "C:/Users/猫的秋刀鱼/Desktop/毕业设计";
const dataRoot = `${projectRoot}/data/zhou_later_pickup`;
const outputDir = `${projectRoot}/output/dataset_artifacts/zhou_later_pickup_20260824`;
const outputPath = `${outputDir}/zhou_later_pickup_dataset.xlsx`;

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  const src = text.replace(/^\uFEFF/, "");
  for (let i = 0; i < src.length; i++) {
    const ch = src[i];
    if (quoted) {
      if (ch === '"' && src[i + 1] === '"') {
        field += '"';
        i++;
      } else if (ch === '"') {
        quoted = false;
      } else {
        field += ch;
      }
    } else if (ch === '"') {
      quoted = true;
    } else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n") {
      row.push(field.replace(/\r$/, ""));
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += ch;
    }
  }
  if (field.length || row.length) {
    row.push(field.replace(/\r$/, ""));
    rows.push(row);
  }
  const headers = rows.shift();
  return rows.filter(r => r.some(v => v !== "")).map(r => Object.fromEntries(headers.map((h, i) => [h, r[i] ?? ""])));
}

const asNumber = value => value === "" || value == null ? "" : Number(value);
const forward = parseCsv(await fs.readFile(`${dataRoot}/forward_nodes_normalized.csv`, "utf8"));
const manifest = parseCsv(await fs.readFile(`${dataRoot}/manifest.csv`, "utf8"));
const metadata = JSON.parse(await fs.readFile(`${dataRoot}/metadata.json`, "utf8"));
const fleetConfig = JSON.parse(await fs.readFile(`${projectRoot}/config/fleet_config.json`, "utf8"));

const workbook = Workbook.create();
const guide = workbook.worksheets.add("说明");
const params = workbook.worksheets.add("参数");
const instances = workbook.worksheets.add("实例清单");
const nodes = workbook.worksheets.add("正向节点");
const pickups = workbook.worksheets.add("回收任务");
const qc = workbook.worksheets.add("质量检查");

const colors = {
  navy: "#17365D",
  teal: "#0F766E",
  tealLight: "#DDF4EE",
  blueLight: "#EAF2F8",
  yellow: "#FFF2CC",
  green: "#E2F0D9",
  red: "#FCE4D6",
  gray: "#F2F2F2",
  line: "#D9E2F3",
  white: "#FFFFFF",
  text: "#1F2937",
};

function titleBand(sheet, range, value) {
  sheet.getRange(range).merge();
  const cell = sheet.getRange(range.split(":")[0]);
  cell.values = [[value]];
  cell.format = {
    fill: colors.navy,
    font: { bold: true, color: colors.white, size: 16 },
    horizontalAlignment: "left",
    verticalAlignment: "center",
  };
  sheet.getRange(range).format.rowHeight = 30;
}

function styleHeader(range) {
  range.format = {
    fill: colors.teal,
    font: { bold: true, color: colors.white },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: colors.line },
  };
  range.format.rowHeight = 32;
}

for (const sheet of [guide, params, instances, nodes, pickups, qc]) sheet.showGridLines = false;

// 说明
titleBand(guide, "A1:F1", "Zhou 正向两级配送 + 延迟包装回收数据集");
guide.getRange("A3:F3").values = [["数据定位", "来源实例", "正向顾客记录", "回收情景", "可运行CSV", "当前模型兼容"]];
styleHeader(guide.getRange("A3:F3"));
guide.getRange("A4:F4").values = [["公开正向数据 + 本文构造回收层", metadata.instance_count, metadata.unique_forward_customer_rows, metadata.scenarios.length, metadata.processed_csv_count, "Models 1–7"]];
guide.getRange("A4:F4").format = { fill: colors.tealLight, font: { bold: true, color: colors.text }, horizontalAlignment: "center" };
guide.getRange("A6:B12").values = [
  ["核心规则", "pickup_ready = delivery_due + service_time + usage_lag"],
  ["回收窗结束", "pickup_due = pickup_ready + pickup_window_width"],
  ["动作语义", "本次配送和本次包装回收是两个独立服务动作；必须先配送、后回收"],
  ["原始 pickup", "不参与新回收任务；仅保存在 source_pickup_ignored 供审计"],
  ["顾客分仓", "Models 1/3/5/6/7 使用最近前置仓预分配；Models 2/4 可重新优化"],
  ["包装假设", `每个顾客1个可循环容器；空箱重量${fleetConfig.container_weight}、体积${fleetConfig.container_volume}，与 fleet_config.json 一致`],
  ["时间单位", "沿用 benchmark 时间单位，并在本文情景中统一解释为分钟"],
];
guide.getRange("A6:A12").format = { fill: colors.blueLight, font: { bold: true, color: colors.navy } };
guide.getRange("B6:B12").format = { wrapText: true, verticalAlignment: "top" };
guide.getRange("A14:B18").values = [
  ["来源仓库", metadata.source_repository],
  ["固定提交", metadata.source_commit],
  ["处理目录", "data/zhou_later_pickup/"],
  ["结构修复", "保留原作者 Java 实际读取的 N+S+1 行及坐标；删除未读取行；卫星统一为 x y 10，仓库统一为 x y 0"],
  ["主实验建议", "17个原始干净实例标记 clean；5个规范化实例标记 repaired，披露修复规则后可合并或分组统计"],
];
guide.getRange("A14:A18").format = { fill: colors.gray, font: { bold: true, color: colors.navy } };
guide.getRange("B14:B18").format = { wrapText: true };
guide.getRange("A17:B18").format.rowHeight = 42;
guide.getRange("A3:F4").format.borders = { preset: "all", style: "thin", color: colors.line };
guide.getRange("A6:B12").format.borders = { preset: "outside", style: "thin", color: colors.line };
guide.getRange("A14:B18").format.borders = { preset: "outside", style: "thin", color: colors.line };
guide.getRange("A1:F18").format.font.name = "Microsoft YaHei";
guide.getRange("A:A").format.columnWidth = 20;
guide.getRange("B:B").format.columnWidth = 72;
guide.getRange("C:F").format.columnWidth = 17;
guide.freezePanes.freezeRows(3);

// 参数
titleBand(params, "A1:F1", "可编辑情景参数与包装假设");
params.getRange("A3:F3").values = [["scenario_id", "中文名称", "usage_lag", "pickup_window_width", "生成公式", "业务解释"]];
styleHeader(params.getRange("A3:F3"));
const scenarioValues = metadata.scenarios.map(s => [s.scenario_id, s.label_zh, s.usage_lag, s.pickup_window_width, "eR=lD+sD+Δ；lR=eR+W", s.interpretation]);
params.getRange(`A4:F${3 + scenarioValues.length}`).values = scenarioValues;
params.getRange("C4:D6").format = { fill: colors.yellow, numberFormat: "0", horizontalAlignment: "right" };
params.getRange("A9:B9").values = [["全局假设", "值"]];
styleHeader(params.getRange("A9:B9"));
params.getRange("A10:B15").values = [
  ["每顾客容器数", 1],
  ["单个空容器重量", fleetConfig.container_weight],
  ["单个空容器体积", fleetConfig.container_volume],
  ["分仓规则", "最近前置仓（欧氏距离）"],
  ["源 pickup 是否用于回收量", "否"],
  ["回收服务时间", "暂与顾客配送服务时间相同（受当前求解器节点服务时间结构限制）"],
];
params.getRange("B10:B12").format = { fill: colors.yellow, numberFormat: "0.0" };
params.getRange("B13:B15").format = { fill: colors.blueLight, wrapText: true };
params.getRange("A1:F15").format.font.name = "Microsoft YaHei";
params.getRange("A:A").format.columnWidth = 25;
params.getRange("B:B").format.columnWidth = 25;
params.getRange("C:D").format.columnWidth = 20;
params.getRange("E:E").format.columnWidth = 26;
params.getRange("F:F").format.columnWidth = 54;
params.tables.add("A3:F6", true, "ScenarioParametersTable").style = "TableStyleMedium2";
params.freezePanes.freezeRows(3);

// 实例清单
const manifestHeaders = ["instance_id", "source_set", "source_instance", "scenario_id", "customer_count", "satellite_count", "e1_vehicle_count", "e1_capacity", "e2_vehicle_count", "e2_vehicles_per_satellite", "e2_capacity", "usage_lag", "pickup_window_width", "processed_file", "source_file", "source_extra_rows_ignored", "source_nonstandard_facility_rows", "source_original_extra_rows", "source_original_nonstandard_facility_rows", "source_repair_applied", "data_quality", "repaired_source_file"];
instances.getRange("A1:V1").values = [manifestHeaders];
styleHeader(instances.getRange("A1:V1"));
const manifestValues = manifest.map(r => [
  r.instance_id, r.source_set, r.source_instance, r.scenario_id,
  asNumber(r.customer_count), asNumber(r.satellite_count), asNumber(r.e1_vehicle_count), asNumber(r.e1_capacity),
  asNumber(r.e2_vehicle_count), asNumber(r.e2_vehicles_per_satellite), asNumber(r.e2_capacity),
  asNumber(r.usage_lag), asNumber(r.pickup_window_width), r.processed_file, r.source_file,
  asNumber(r.source_extra_rows_ignored), asNumber(r.source_nonstandard_facility_rows),
  asNumber(r.source_original_extra_rows), asNumber(r.source_original_nonstandard_facility_rows),
  r.source_repair_applied === "True", r.data_quality, r.repaired_source_file,
]);
instances.getRange(`A2:V${1 + manifestValues.length}`).values = manifestValues;
instances.getRange(`E2:M${1 + manifestValues.length}`).format.numberFormat = "0";
instances.getRange(`P2:S${1 + manifestValues.length}`).format.numberFormat = "0";
instances.getRange(`U2:U${1 + manifestValues.length}`).conditionalFormats.add("containsText", { text: "clean", format: { fill: colors.green, font: { color: "#375623" } } });
instances.getRange(`U2:U${1 + manifestValues.length}`).conditionalFormats.add("containsText", { text: "repaired", format: { fill: colors.yellow, font: { color: "#7F6000" } } });
instances.tables.add(`A1:V${1 + manifestValues.length}`, true, "InstanceManifestTable").style = "TableStyleMedium2";
instances.freezePanes.freezeRows(1);
instances.freezePanes.freezeColumns(4);
instances.getRange("A:V").format.font.name = "Microsoft YaHei";
const instanceWidths = [22, 12, 18, 23, 14, 14, 16, 13, 16, 24, 13, 14, 22, 45, 55, 25, 30, 25, 34, 22, 16, 42];
instanceWidths.forEach((w, i) => instances.getRangeByIndexes(0, i, 1, 1).format.columnWidth = w);

// 正向节点
const nodeHeaders = ["instance_id", "source_set", "source_instance", "node_id", "node_type", "x", "y", "delivery_demand", "delivery_ready", "delivery_due", "service_time", "assigned_sat", "source_pickup_ignored", "data_origin"];
nodes.getRange("A1:N1").values = [nodeHeaders];
styleHeader(nodes.getRange("A1:N1"));
const nodeValues = forward.map(r => [
  r.instance_id, r.source_set, r.source_instance, r.node_id, r.node_type,
  asNumber(r.x), asNumber(r.y), asNumber(r.delivery_demand), asNumber(r.delivery_ready), asNumber(r.delivery_due),
  asNumber(r.service_time), r.assigned_sat, asNumber(r.source_pickup_ignored), r.data_origin,
]);
nodes.getRange(`A2:N${1 + nodeValues.length}`).values = nodeValues;
nodes.getRange(`F2:M${1 + nodeValues.length}`).format.numberFormat = "0.0";
nodes.tables.add(`A1:N${1 + nodeValues.length}`, true, "ForwardNodesTable").style = "TableStyleMedium2";
nodes.freezePanes.freezeRows(1);
nodes.freezePanes.freezeColumns(5);
nodes.getRange("A:N").format.font.name = "Microsoft YaHei";
const nodeWidths = [22, 12, 18, 12, 12, 10, 10, 18, 16, 14, 14, 14, 22, 18];
nodeWidths.forEach((w, i) => nodes.getRangeByIndexes(0, i, 1, 1).format.columnWidth = w);

// 回收任务（每个正向顾客 × 三种情景）
const pickupHeaders = ["instance_id", "scenario_id", "customer_id", "x", "y", "assigned_sat", "delivery_ready", "delivery_due", "service_time", "usage_lag", "pickup_window_width", "pickup_ready", "pickup_due", "container_count", "pickup_weight", "pickup_volume", "delivery_task_id", "pickup_task_id", "precedence_check", "source_pickup_ignored", "precedence_rule"];
pickups.getRange("A1:U1").values = [pickupHeaders];
styleHeader(pickups.getRange("A1:U1"));
const customerRows = forward.filter(r => r.node_type === "c");
const pickupValues = [];
const pickupFormulaRows = [];
const scenarioParamRow = { short_same_day: 4, standard_same_day: 5, next_day: 6 };
let excelRow = 2;
for (const scenario of metadata.scenarios) {
  const pRow = scenarioParamRow[scenario.scenario_id];
  for (const r of customerRows) {
    pickupValues.push([
      r.instance_id, scenario.scenario_id, r.node_id, asNumber(r.x), asNumber(r.y), r.assigned_sat,
      asNumber(r.delivery_ready), asNumber(r.delivery_due), asNumber(r.service_time),
      "", "", "", "", "", "", "", "", "", "", asNumber(r.source_pickup_ignored),
      "pickup_after_delivery_completion_plus_usage_lag",
    ]);
    pickupFormulaRows.push([
      `='参数'!$C$${pRow}`,
      `='参数'!$D$${pRow}`,
      `=H${excelRow}+I${excelRow}+J${excelRow}`,
      `=L${excelRow}+K${excelRow}`,
      "='参数'!$B$10",
      `=N${excelRow}*'参数'!$B$11`,
      `=N${excelRow}*'参数'!$B$12`,
      `=C${excelRow}&\"d\"`,
      `=C${excelRow}&\"p\"`,
      `=IF(AND(L${excelRow}>=H${excelRow}+I${excelRow}+J${excelRow},M${excelRow}>L${excelRow},F${excelRow}<>\"\"),\"PASS\",\"FAIL\")`,
    ]);
    excelRow++;
  }
}
const pickupLastRow = 1 + pickupValues.length;
pickups.getRange(`A2:U${pickupLastRow}`).values = pickupValues;
pickups.getRange(`J2:S${pickupLastRow}`).formulas = pickupFormulaRows;
pickups.getRange(`D2:P${pickupLastRow}`).format.numberFormat = "0.0";
pickups.getRange(`J2:P${pickupLastRow}`).format.fill = colors.blueLight;
pickups.getRange(`S2:S${pickupLastRow}`).conditionalFormats.add("containsText", { text: "PASS", format: { fill: colors.green, font: { color: "#375623" } } });
pickups.getRange(`S2:S${pickupLastRow}`).conditionalFormats.add("containsText", { text: "FAIL", format: { fill: colors.red, font: { color: "#9C0006" } } });
pickups.tables.add(`A1:U${pickupLastRow}`, true, "RecoveryTasksTable").style = "TableStyleMedium2";
pickups.freezePanes.freezeRows(1);
pickups.freezePanes.freezeColumns(6);
pickups.getRange("A:U").format.font.name = "Microsoft YaHei";
const pickupWidths = [22, 23, 13, 9, 9, 14, 16, 14, 13, 13, 22, 14, 13, 16, 14, 14, 18, 16, 18, 22, 48];
pickupWidths.forEach((w, i) => pickups.getRangeByIndexes(0, i, 1, 1).format.columnWidth = w);

// 质量检查
titleBand(qc, "A1:E1", "数据质量与完整性检查");
qc.getRange("A3:E3").values = [["检查项", "期望值", "实际值", "状态", "说明"]];
styleHeader(qc.getRange("A3:E3"));
const forwardLastRow = 1 + nodeValues.length;
const manifestLastRow = 1 + manifestValues.length;
qc.getRange("A4:B12").values = [
  ["正向顾客记录数", metadata.unique_forward_customer_rows],
  ["实例—情景文件数", metadata.processed_csv_count],
  ["回收任务记录数", metadata.scenario_customer_rows],
  ["先送后取检查通过数", metadata.scenario_customer_rows],
  ["修复后结构合规的实例—情景数", 120],
  ["原始干净实例—情景数", 105],
  ["规范化修复实例—情景数", 15],
  ["规范化源文件数", 5],
  ["回收周期情景数", metadata.scenarios.length],
];
qc.getRange("C4:C12").formulas = [
  [`=COUNTIF('正向节点'!$E$2:$E$${forwardLastRow},\"c\")`],
  [`=COUNTA('实例清单'!$A$2:$A$${manifestLastRow})`],
  [`=COUNTA('回收任务'!$A$2:$A$${pickupLastRow})`],
  [`=COUNTIF('回收任务'!$S$2:$S$${pickupLastRow},\"PASS\")`],
  [`=COUNTIFS('实例清单'!$P$2:$P$${manifestLastRow},0,'实例清单'!$Q$2:$Q$${manifestLastRow},0)`],
  [`=COUNTIF('实例清单'!$U$2:$U$${manifestLastRow},\"clean\")`],
  [`=COUNTIF('实例清单'!$U$2:$U$${manifestLastRow},\"repaired\")`],
  [`=COUNTIF('实例清单'!$U$2:$U$${manifestLastRow},\"repaired\")/3`],
  ["=COUNTA('参数'!$A$4:$A$6)"],
];
qc.getRange("D4").formulas = [["=IF(B4=C4,\"PASS\",\"FAIL\")"]];
qc.getRange("D4:D12").fillDown();
qc.getRange("E4:E12").values = [
  ["40个来源实例合计的唯一顾客记录"],
  ["40个实例 × 3种回收周期"],
  ["每个正向顾客在每种回收周期下生成1条回收任务"],
  ["pickup_ready 不早于最迟配送完成时刻加使用周期"],
  ["求解文件均无额外行或非标准设施列"],
  ["17个原始结构正常实例 × 3种周期"],
  ["5个Set1规范化实例 × 3种周期，保留原始异常审计字段"],
  ["按作者Java读取语义规范为精确的 N+S+1 行"],
  ["同日短周期、同日标准周期、次日回收"],
];
qc.getRange("B4:C12").format.numberFormat = "0";
qc.getRange("D4:D12").conditionalFormats.add("containsText", { text: "PASS", format: { fill: colors.green, font: { bold: true, color: "#375623" } } });
qc.getRange("D4:D12").conditionalFormats.add("containsText", { text: "FAIL", format: { fill: colors.red, font: { bold: true, color: "#9C0006" } } });
qc.getRange("A3:E12").format.borders = { preset: "all", style: "thin", color: colors.line };
qc.getRange("A1:E12").format.font.name = "Microsoft YaHei";
qc.getRange("A:A").format.columnWidth = 32;
qc.getRange("B:D").format.columnWidth = 15;
qc.getRange("E:E").format.columnWidth = 66;
qc.getRange("E4:E12").format.wrapText = true;
qc.freezePanes.freezeRows(3);

// Compact verification before export.
const checks = [];
checks.push(await workbook.inspect({ kind: "table", range: "质量检查!A1:E12", include: "values,formulas", tableMaxRows: 14, tableMaxCols: 8 }));
checks.push(await workbook.inspect({ kind: "table", range: "回收任务!A1:U8", include: "values,formulas", tableMaxRows: 10, tableMaxCols: 24 }));
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});

await fs.mkdir(outputDir, { recursive: true });
for (const [sheetName, range] of [
  ["说明", "A1:F18"],
  ["参数", "A1:F15"],
  ["实例清单", "A1:V12"],
  ["正向节点", "A1:N12"],
  ["回收任务", "A1:U12"],
  ["质量检查", "A1:E12"],
]) {
  const preview = await workbook.render({ sheetName, range, scale: 1, format: "png" });
  const safe = sheetName.replace(/[^\p{L}\p{N}]+/gu, "_");
  await fs.writeFile(path.join(outputDir, `preview_${safe}.png`), new Uint8Array(await preview.arrayBuffer()));
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

console.log(JSON.stringify({
  outputPath,
  sheets: ["说明", "参数", "实例清单", "正向节点", "回收任务", "质量检查"],
  forwardRows: forward.length,
  recoveryRows: pickupValues.length,
  manifestRows: manifest.length,
  inspections: checks.map(c => c.ndjson),
  formulaErrors: errors.ndjson,
}, null, 2));
