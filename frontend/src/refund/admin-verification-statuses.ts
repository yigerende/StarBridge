export function parseVerificationStatuses(value: string): number[] {
  if (!value.trim()) return [];
  if (!/^\s*\d{3}(?:\s*[,，]\s*\d{3})*\s*$/.test(value)) {
    throw new Error("请填写三位状态码，多个用英文逗号分隔，例如 404,409");
  }
  const codes = value.split(/[,，]/).map(Number);
  if (codes.length > 30 || codes.some(code => code < 400 || code > 599) || new Set(codes).size !== codes.length) {
    throw new Error("请填写最多 30 个不重复的 400～599 状态码");
  }
  return codes;
}
