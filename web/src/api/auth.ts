import { request } from './client'

export interface TeamView {
  tenant_id: string
  tenant_name: string
  role: string
}

export interface SessionView {
  account: { id: string; email: string }
  teams: TeamView[]
  current_tenant_id: string | null
}

export function login(email: string, password: string): Promise<SessionView> {
  return request('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })
}

export function logout(): Promise<void> {
  return request('/api/auth/logout', { method: 'POST' })
}

export function fetchSession(): Promise<SessionView> {
  return request('/api/auth/session')
}

export function switchTeam(tenantId: string): Promise<SessionView> {
  return request('/api/auth/switch-team', {
    method: 'POST',
    body: JSON.stringify({ tenant_id: tenantId }),
  })
}
