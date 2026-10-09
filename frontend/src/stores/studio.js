import { defineStore } from 'pinia'
import { reactive } from 'vue'
import {
  getNotebooks,
  createNotebook,
  getNotebook,
  updateNotebook,
  deleteNotebook,
  getNotebookNotes,
  createNotebookNote,
  updateNote,
  deleteNote,
  createNoteFromChat,
  saveNoteAsDocument,
  getChatMessages,
} from '../api'
import { askStream, agentAskStream } from '../api/chatStream'

// 记住上次打开的工作区，进入工作台默认恢复，避免每次手动切换
const LAST_NB_KEY = 'studio:lastNotebookId'
// 会话历史：每个工作区对应一个会话（localStorage 记 session_id），切回可续聊
const SID_KEY = (nbId) => `studio:sid:${nbId}`

function saveLastNotebook(nbId) {
  if (nbId) localStorage.setItem(LAST_NB_KEY, String(nbId))
}

export const useStudioStore = defineStore('studio', {
  state: () => ({
    notebooks: [], // Notebook 列表
    currentNotebook: null, // 当前打开的 Notebook
    notes: [], // 当前工作区的内容列表
    selectedNoteId: null, // 右侧选中的内容
    activeTab: 'notes', // 右侧列标签：'notes' | 'reports'
    // 源（KB + 文件），自动跟随当前工作区的 source_kb_ids/source_file_ids 加载
    selectedKbIds: [],
    selectedFileIds: [],
    // 加载状态
    loadingNotebooks: false,
    loadingNotes: false,
    // 各工作区的对话状态（按 notebookId 隔离）。
    // 放在 store 而非组件内：切换工作区/离开页面时流式回答不中断，回来可直接看到结果。
    chats: {}, // { [nbId]: { messages, loading, sessionId, abortCtrl, loaded } }
  }),

  getters: {
    selectedNote(state) {
      return state.notes.find((n) => n.id === state.selectedNoteId) || null
    },
  },

  actions: {
    async loadNotebooks() {
      this.loadingNotebooks = true
      try {
        const res = await getNotebooks()
        this.notebooks = Array.isArray(res) ? res : []
      } finally {
        this.loadingNotebooks = false
      }
    },

    /** 初始化：优先恢复上次打开的工作区；否则打开最近更新的；
     *  一个工作区都没有（新用户）时自动创建"未命名工作区"直接开始，随时可重命名。 */
    async initNotebooks() {
      await this.loadNotebooks()
      const saved = localStorage.getItem(LAST_NB_KEY)
      if (saved && this.notebooks.some((nb) => String(nb.id) === saved)) {
        await this.openNotebook(Number(saved))
        return
      }
      if (this.notebooks.length) {
        // 列表按 updated_at 降序：默认打开最近更新的
        await this.openNotebook(this.notebooks[0].id)
        return
      }
      // 新用户/无工作区：自动建一个直接开始工作
      const nb = await this.createNotebook({ name: '未命名工作区' })
      await this.openNotebook(nb.id)
    },

    async openNotebook(notebookId) {
      // 拉详情（含 source_kb_ids / source_file_ids）
      const nb = await getNotebook(notebookId)
      this.currentNotebook = nb
      this.selectedKbIds = [...(nb.source_kb_ids || [])]
      this.selectedFileIds = [...(nb.source_file_ids || [])]
      saveLastNotebook(nb.id)
      await this.loadNotes(notebookId)
    },

    async createNotebook(data) {
      const nb = await createNotebook(data)
      await this.loadNotebooks()
      return nb
    },

    async updateCurrentNotebook(data) {
      if (!this.currentNotebook) return
      const updated = await updateNotebook(this.currentNotebook.id, data)
      this.currentNotebook = { ...this.currentNotebook, ...updated }
      // 同步源选择（用户改了 notebook.source_kb_ids 时）
      if (data.source_kb_ids !== undefined) this.selectedKbIds = [...data.source_kb_ids]
      if (data.source_file_ids !== undefined) this.selectedFileIds = [...data.source_file_ids]
      await this.loadNotebooks()
    },

    async deleteCurrentNotebook() {
      if (!this.currentNotebook) return
      await deleteNotebook(this.currentNotebook.id)
      this.currentNotebook = null
      this.notes = []
      this.selectedNoteId = null
      await this.loadNotebooks()
    },

    async loadNotes(notebookId) {
      const id = notebookId || this.currentNotebook?.id
      if (!id) return
      this.loadingNotes = true
      try {
        this.notes = await getNotebookNotes(id)
        if (!this.selectedNoteId && this.notes.length) {
          this.selectedNoteId = this.notes[0].id
        }
      } finally {
        this.loadingNotes = false
      }
    },

    async createNote(data) {
      if (!this.currentNotebook) return
      const note = await createNotebookNote(this.currentNotebook.id, data)
      await this.loadNotes()
      this.selectedNoteId = note.id
      return note
    },

    async updateNote(noteId, data) {
      const updated = await updateNote(noteId, data)
      await this.loadNotes()
      this.selectedNoteId = updated.id
      return updated
    },

    async deleteNote(noteId) {
      await deleteNote(noteId)
      if (this.selectedNoteId === noteId) this.selectedNoteId = null
      await this.loadNotes()
    },

    /** 从 chat 助手消息生成 note */
    async saveChatAsNote({ question, answer, sources, session_id, query_log_id }) {
      if (!this.currentNotebook) return
      const note = await createNoteFromChat({
        notebook_id: this.currentNotebook.id,
        session_id,
        query_log_id,
        question,
        answer,
        sources,
      })
      await this.loadNotes()
      this.selectedNoteId = note.id
      return note
    },

    /** 把 note 转 KB 文档 */
    async saveAsDocument(noteId, kbId) {
      return await saveNoteAsDocument(noteId, kbId)
    },

    // 源选择变化时（用户手动勾选 KB / 文件）
    setSelectedSources({ kbIds, fileIds }) {
      if (kbIds !== undefined) this.selectedKbIds = [...kbIds]
      if (fileIds !== undefined) this.selectedFileIds = [...fileIds]
    },

    // ========== 对话（流式，按工作区隔离，跨页面保持） ==========
    /** 取（或建）某工作区的对话状态 */
    chatOf(nbId) {
      if (!nbId) return null
      if (!this.chats[nbId]) {
        const sid = localStorage.getItem(SID_KEY(nbId))
        this.chats[nbId] = {
          messages: [],
          loading: false,
          sessionId: sid ? Number(sid) : null,
          abortCtrl: null,
          loaded: false, // 是否已从后端拉过历史（避免覆盖运行中的对话）
        }
      }
      return this.chats[nbId]
    },

    /** 打开工作区时恢复该工作区的历史会话消息（已有运行中/已加载状态则不覆盖） */
    async loadChatHistory(nbId) {
      const chat = this.chatOf(nbId)
      if (!chat || chat.loaded || chat.loading) return
      chat.loaded = true
      if (!chat.sessionId) {
        chat.messages = []
        return
      }
      try {
        const res = await getChatMessages(chat.sessionId)
        chat.messages = (Array.isArray(res) ? res : (res?.items || [])).map((m) => ({
          role: m.role === 'user' ? 'user' : 'ai',
          content: m.content || '',
          sources: m.sources || [],
          showSources: false,
          queryLogId: m.query_log_id || null,
          feedback: null,
          tools: [],
          thinking: false,
        }))
      } catch {
        chat.messages = []
      }
    },

    /** 发送问题并流式接收回答。整个循环在 store 内执行，组件卸载不影响。
     *  agent=true 时走 dsh Agent 模式（/ask/agent/stream）：不传 kb_ids/file_ids/thinking，
     *  token 按块到达；同一 chat session 两种模式可混用。 */
    async sendChat(nbId, { question: q, kbIds, fileIds, thinking, agent }) {
      const chat = this.chatOf(nbId)
      if (!chat || !q || chat.loading) return
      chat.messages.push({ role: 'user', content: q })
      // 必须用 reactive：流式过程中 aiMsg.content 逐 token 变更，普通对象直接改不触发重绘
      // 发送后立即进入 thinking 状态并显示阶段提示，避免"发出去毫无动静"
      const aiMsg = reactive({ role: 'ai', content: '', sources: [], showSources: false, queryLogId: null, tools: [], thinking: true, statusText: '正在思考…' })
      chat.messages.push(aiMsg)
      chat.loading = true
      const ctrl = new AbortController()
      chat.abortCtrl = ctrl
      try {
        const stream = agent
          ? agentAskStream({ question: q, session_id: chat.sessionId, kb_ids: kbIds, file_ids: fileIds, signal: ctrl.signal })
          : askStream({
              question: q,
              kb_ids: kbIds,
              file_ids: fileIds,
              session_id: chat.sessionId,
              thinking,
              signal: ctrl.signal,
            })
        for await (const frame of stream) {
          if (frame.type === 'meta') {
            // 新会话的 session_id 由后端创建，记到当前 notebook 下以便下次续聊
            if (frame.session_id) {
              chat.sessionId = frame.session_id
              localStorage.setItem(SID_KEY(nbId), String(frame.session_id))
            }
          } else if (frame.type === 'status') {
            // 后端阶段提示（如"正在检索知识库…"）
            aiMsg.statusText = frame.text || '正在思考…'
          } else if (frame.type === 'sources') {
            aiMsg.sources = frame.sources || []
            aiMsg.statusText = '正在生成回答…'
          } else if (frame.type === 'token') {
            aiMsg.thinking = false
            aiMsg.statusText = null
            aiMsg.content += frame.content || ''
          } else if (frame.type === 'done') {
            aiMsg.thinking = false
            aiMsg.statusText = null
            aiMsg.queryLogId = frame.query_log_id
          } else if (frame.type === 'thinking') {
            aiMsg.thinking = frame.status === 'start'
            if (!aiMsg.thinking) aiMsg.statusText = null
          } else if (frame.type === 'tool') {
            // 连续调用同一工具时合并成一个 chip：total 计数 + tick 驱动 ×N 弹跳动画；
            // active 表示该组合里最近一次调用仍在进行
            if (frame.status === 'start') {
              const last = aiMsg.tools[aiMsg.tools.length - 1]
              if (last && last.name === frame.name && !last.active) {
                last.total++
                last.active = true
                last.callId = frame.call_id || last.callId
                last.tick++
              } else {
                aiMsg.tools.push({ name: frame.name, total: 1, done: 0, failed: 0, active: true, callId: frame.call_id || null, tick: 0 })
              }
            } else if (frame.status === 'done') {
              // agent 模式的 tool 帧带 call_id；无 call_id 时退回按 name 匹配（兼容普通模式）
              const entry = [...aiMsg.tools].reverse().find((x) =>
                (frame.call_id ? x.callId === frame.call_id : x.name === frame.name) && x.active)
              if (entry) {
                if (frame.is_error) entry.failed++
                else entry.done++
                // 失败原因（后端从 ACP 事件 content 提取），chip 上以 tooltip 展示
                if (frame.error) entry.error = frame.error
                entry.active = false
                entry.tick++
              }
            }
          } else if (frame.type === 'error') {
            aiMsg.thinking = false
            aiMsg.statusText = null
            aiMsg.content += `\n\n[错误] ${frame.detail}`
          }
        }
      } catch (err) {
        // 主动 abort（用户点停止/删除工作区）不视为错误
        if (!(err?.name === 'AbortError' || err?.message?.includes('aborted'))) {
          aiMsg.content += `\n\n[网络错误] ${err?.message || err}`
        }
      } finally {
        chat.loading = false
        chat.abortCtrl = null
      }
    },

    /** 停止某工作区正在进行的回答 */
    stopChat(nbId) {
      this.chats[nbId]?.abortCtrl?.abort()
    },

    /** 删除工作区时清理其对话状态（中断进行中的流） */
    clearChat(nbId) {
      const chat = this.chats[nbId]
      if (chat) {
        chat.abortCtrl?.abort()
        delete this.chats[nbId]
      }
      localStorage.removeItem(SID_KEY(nbId))
    },
  },
})