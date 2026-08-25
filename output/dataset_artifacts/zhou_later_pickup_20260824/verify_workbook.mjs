import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const base = "C:/Users/猫的秋刀鱼/Desktop/毕业设计/output/dataset_artifacts/zhou_later_pickup_20260824";
const file = `${base}/zhou_later_pickup_dataset.xlsx`;
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(file));

const qc = await workbook.inspect({
  kind: "table",
  range: "质量检查!A1:E10",
  include: "values,formulas",
  tableMaxRows: 12,
  tableMaxCols: 8,
});
const sample = await workbook.inspect({
  kind: "table",
  range: "回收任务!A1:U5",
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 24,
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});

const preview = await workbook.render({
  sheetName: "参数",
  range: "A1:F15",
  scale: 1,
  format: "png",
});
await fs.writeFile(`${base}/preview_before_参数.png`, new Uint8Array(await preview.arrayBuffer()));

await fs.writeFile(
  `${base}/verify_report.json`,
  JSON.stringify({ qc: qc.ndjson, sample: sample.ndjson, errors: errors.ndjson }, null, 2),
  "utf8",
);
