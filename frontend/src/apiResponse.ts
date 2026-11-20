// A gateway can replace a JSON error with an HTML page. Never show that page
// (or the JSON parser's exception) as a business error.
export async function readApiResponse(response: Response) {
  let value: any
  try {
    value = await response.json()
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error
    const message = response.status === 504 ? '请求超时，请稍后重试'
      : response.status === 401 ? '登录已失效，请重新登录'
      : response.status === 403 ? '没有权限执行此操作'
      : response.ok ? '服务器返回的数据格式异常，请稍后重试'
      : '请求未完成，请稍后重试'
    throw new Error(`${message}（HTTP ${response.status}）`)
  }
  if (!response.ok) throw new Error(typeof value?.error === 'string' && value.error ? value.error : `请求未完成（HTTP ${response.status}）`)
  return value
}
