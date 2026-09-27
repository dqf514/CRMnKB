// 流式问答 SSE 封装（axios 不支持流式 POST，改用 fetch + ReadableStream）
// 帧格式：data: {"type":"meta"|"sources"|"status"|"thinking"|"tool"|"token"|"done"|"error", ...}

// 公共的 SSE POST + 解析生成器：逐帧 yield JSON，心跳注释行（: ping）自然被忽略
async function* _ssePost(url, body, signal) {
  const token = localStorage.getItem('token')
  const resp = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  })

  if (!resp.ok) {
    let detail = `请求失败（${resp.status}）`
    try {
      const err = await resp.json()
      if (typeof err?.detail === 'string') detail = err.detail
    } catch { /* 忽略 */ }
    if (resp.status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      window.location.href = '/login'
    }
    throw new Error(detail)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split('\n\n')
    buffer = frames.pop()
    for (const frame of frames) {
      for (const line of frame.split('\n')) {
        if (!line.startsWith('data:')) continue
        const payload = line.slice(5).trim()
        if (!payload) continue
        try {
          yield JSON.parse(payload)
        } catch {
          /* 非 JSON 帧忽略 */
        }
      }
    }
  }
}

// 普通模式：本地 RAG 管线，逐 token 到达
export function askStream({ session_id, question, top_k, kb_ids, file_ids, thinking, signal }) {
  return _ssePost('/api/v1/chat/ask/stream', {
    session_id: session_id ?? undefined,
    question,
    top_k,
    kb_ids: kb_ids?.length ? kb_ids : undefined,
    file_ids: file_ids?.length ? file_ids : undefined,
    thinking,
  }, signal)
}

// dsh Agent 模式：由 dsh 自主规划检索，不接受 kb_ids/file_ids/thinking；
// 帧约定与 askStream 一致，token 帧按块到达（每个 agent step 一整块 Markdown），
// tool 帧额外带 call_id/is_error，done 帧带可选 finish_reason
export function agentAskStream({ session_id, question, signal }) {
  return _ssePost('/api/v1/chat/ask/agent/stream', {
    session_id: session_id ?? undefined,
    question,
  }, signal)
}
