export interface Account {
  email: string;
  password: string;
  totp_secret: string;
}
// RFC 4180 quoting, including embedded commas, quotes and newlines.
function csv(text: string, delimiter: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  let closed = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          quoted = false;
          closed = true;
        }
      } else field += c;
      continue;
    }
    if (c === '"') {
      if (field || closed) throw new Error("CSV 引号格式不正确");
      quoted = true;
    } else if (c === delimiter) {
      row.push(field);
      field = "";
      closed = false;
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
      closed = false;
    } else {
      if (closed) throw new Error("CSV 引号后应为分隔符或换行");
      field += c;
    }
  }
  if (quoted) throw new Error("CSV 双引号未闭合");
  if (field || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}
export function parseAccounts(input: string): Account[] {
  const text = input.replace(/^\uFEFF/, "");
  if (!text.trim()) return [];
  if (new TextEncoder().encode(text).length > 1024 * 1024)
    throw new Error("文件不能超过 1 MB");
  const first = text.split(/\r?\n/).find((line) => line.trim()) || "";
  let rows: string[][];
  if (first.includes("\t")) rows = csv(text, "\t");
  else if (first.includes(",") || first.startsWith('"')) rows = csv(text, ",");
  else
    rows = text
      .split(/\r?\n/)
      .filter((line) => line.trim())
      .map((line) => line.split(/-{3,}/));
  rows = rows.filter((row) => row.some((value) => value.trim()));
  if (
    rows[0] &&
    /^(email|邮箱|账号邮箱)$/i.test(rows[0][0].trim()) &&
    /^(password|密码|账号密码)$/i.test(rows[0][1]?.trim())
  )
    rows.shift();
  if (rows.length > 500) throw new Error("每批最多 500 个账号");
  const seen = new Set<string>();
  return rows.map((row, index) => {
    if (row.length !== 3)
      throw new Error(
        `第 ${index + 1} 行需要邮箱、密码、2FA 三列；密码含分隔符时请使用 CSV`,
      );
    const email = row[0].trim().toLowerCase(),
      password = row[1],
      totp_secret = row[2].trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))
      throw new Error(`第 ${index + 1} 行邮箱格式不正确`);
    if (!password || !totp_secret)
      throw new Error(`第 ${index + 1} 行密码或 2FA 为空`);
    if (seen.has(email)) throw new Error(`第 ${index + 1} 行账号重复`);
    seen.add(email);
    return { email, password, totp_secret };
  });
}
