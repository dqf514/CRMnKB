// Agent 审批封装：dsh Agent 模式的敏感工具调用（写跟进/发邮件）需 admin 审批后执行
// admin 看全租户审批单，普通用户只看自己发起的；decide 仅 admin 可调
import request from './index'

// params.status：pending / executed / rejected / failed，不传为全部
export const getAgentApprovals = (params) => request.get('/api/v1/agent-approvals', { params })

// 审批决策：data = { decision: 'approve' | 'reject', reason?: string }
export const decideAgentApproval = (id, data) => request.post(`/api/v1/agent-approvals/${id}/decide`, data)
