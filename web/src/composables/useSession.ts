import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useRouter } from 'vue-router'

import { ApiClientError } from '../api/client'
import { fetchSession, login, logout, switchTeam } from '../api/auth'
import type { SessionView } from '../api/auth'

export const sessionKey = ['session'] as const

// A missing session is data (null), not an error, so the cache can hold it.
async function sessionFetcher(): Promise<SessionView | null> {
  try {
    return await fetchSession()
  } catch (error) {
    if (error instanceof ApiClientError && error.status === 401) return null
    throw error
  }
}

export { sessionFetcher }

export function useSessionQuery() {
  return useQuery({
    queryKey: sessionKey,
    queryFn: sessionFetcher,
    retry: false,
    staleTime: 60_000,
  })
}

export function useLogin() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { email: string; password: string }) => login(input.email, input.password),
    onSuccess: (session: SessionView) => queryClient.setQueryData(sessionKey, session),
  })
}

export function useLogout() {
  const queryClient = useQueryClient()
  const router = useRouter()
  return useMutation({
    mutationFn: () => logout(),
    onSettled: () => {
      queryClient.setQueryData(sessionKey, null)
      router.push({ name: 'login' })
    },
  })
}

export function useSwitchTeam() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (tenantId: string) => switchTeam(tenantId),
    onSuccess: (session: SessionView) => queryClient.setQueryData(sessionKey, session),
  })
}
