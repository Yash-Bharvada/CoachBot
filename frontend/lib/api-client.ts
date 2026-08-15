'use client'

export type AppEnv = 'development' | 'staging' | 'production'

export interface ApiErrorResponse {
  detail:
    | string
    | Array<{ loc: Array<string | number>; msg: string; type: string }>
    | { error: string; message: string; details?: Record<string, unknown> }
}

export class ApiError extends Error {
  public readonly status: number
  public readonly statusText: string
  public readonly data: ApiErrorResponse | null
  public readonly url: string
  public readonly requestId?: string

  constructor(
    status: number,
    statusText: string,
    data: ApiErrorResponse | null,
    url: string,
    requestId?: string,
  ) {
    const message = extractHumanMessage(data) || statusText || `Request failed (${status})`
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.statusText = statusText
    this.data = data
    this.url = url
    this.requestId = requestId
  }

  get isUnauthorized(): boolean {
    return this.status === 401
  }

  get isForbidden(): boolean {
    return this.status === 403
  }

  get isNotFound(): boolean {
    return this.status === 404
  }

  get isRateLimited(): boolean {
    return this.status === 429
  }

  get isServerError(): boolean {
    return this.status >= 500
  }
}

function extractHumanMessage(data: ApiErrorResponse | Record<string, any> | null): string | null {
  if (!data) return null
  const dataObj = data as Record<string, any>
  if (typeof dataObj.message === 'string' && dataObj.message) {
    return dataObj.message
  }
  if (typeof dataObj.error === 'string' && dataObj.error) {
    return dataObj.message ? `${dataObj.error}: ${dataObj.message}` : dataObj.error
  }
  if (typeof data.detail === 'string') return data.detail
  if (Array.isArray(data.detail)) {
    const first = data.detail[0]
    if (first) {
      const path = first.loc?.slice(1).join('.') || 'field'
      return `${path}: ${first.msg}`
    }
  }
  if (typeof data.detail === 'object' && data.detail !== null) {
    if ('message' in data.detail && typeof data.detail.message === 'string') {
      return data.detail.message
    }
    if ('error' in data.detail && typeof data.detail.error === 'string') {
      return data.detail.error
    }
  }
  return null
}

export interface RequestOptions extends Omit<RequestInit, 'body' | 'headers'> {
  body?: unknown
  headers?: Record<string, string>
  timeoutMs?: number
  retries?: number
  authToken?: string | null
  skipAuth?: boolean
}

const DEFAULT_TIMEOUT_MS = 30_000
const DEFAULT_RETRIES = 1

function getBackendBaseUrl(): string {
  if (typeof window === 'undefined') {
    return (
      process.env.BACKEND_INTERNAL_URL ||
      process.env.NEXT_PUBLIC_BACKEND_URL ||
      'http://localhost:8000'
    )
  }
  return process.env.NEXT_PUBLIC_BACKEND_URL || ''
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function executeRequest(
  path: string,
  options: RequestOptions,
  attempt: number,
): Promise<Response> {
  const {
    body,
    headers: customHeaders,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    authToken,
    skipAuth,
    retries = DEFAULT_RETRIES,
    ...fetchOptions
  } = options

  const base = getBackendBaseUrl()
  const url = base + path

  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...customHeaders,
  }

  let serializedBody: BodyInit | undefined
  if (body !== undefined) {
    if (body instanceof FormData) {
      serializedBody = body
    } else if (body instanceof ArrayBuffer || body instanceof Blob) {
      serializedBody = body
    } else {
      headers['Content-Type'] = 'application/json'
      serializedBody = JSON.stringify(body)
    }
  }

  if (!skipAuth && authToken) {
    headers['Authorization'] = `Bearer ${authToken}`
  }

  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(url, {
      ...fetchOptions,
      headers,
      body: serializedBody,
      signal: controller.signal,
      credentials: 'include',
    })
    clearTimeout(timeoutId)

    if (!response.ok) {
      if (
        attempt < retries &&
        (response.status === 408 || response.status === 429 || response.status >= 500)
      ) {
        const backoff = Math.min(1000 * Math.pow(2, attempt), 5000)
        await sleep(backoff)
        return executeRequest(path, options, attempt + 1)
      }

      let data: ApiErrorResponse | null = null
      try {
        data = (await response.json()) as ApiErrorResponse
      } catch {
        /* swallow parse errors on error responses */
      }
      const requestId = response.headers.get('X-Request-ID') ?? undefined
      throw new ApiError(response.status, response.statusText, data, url, requestId)
    }

    return response
  } catch (error) {
    clearTimeout(timeoutId)
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(408, 'Request Timeout', null, url)
    }
    if (attempt < retries) {
      const backoff = Math.min(1000 * Math.pow(2, attempt), 5000)
      await sleep(backoff)
      return executeRequest(path, options, attempt + 1)
    }
    throw new ApiError(0, 'Network Error', null, url)
  }
}

export async function apiFetch<T = unknown>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const response = await executeRequest(path, options, 0)
  const contentType = response.headers.get('content-type') ?? ''
  if (contentType.includes('application/json')) {
    return (await response.json()) as T
  }
  return (await response.text()) as unknown as T
}

export async function apiFetchBlob(
  path: string,
  options: RequestOptions = {},
): Promise<Blob> {
  const response = await executeRequest(path, options, 0)
  return await response.blob()
}

// High-level API helpers for Interview Prep Simulator

export interface TavusConversationResponse {
  conversation_id: string
  conversation_name?: string
  conversation_url: string
  status: string
  created_at: string
}

export interface InterviewSessionSummary {
  interview_id: string
  status: string
  turn_count: number
  difficulty_current: string
  competencies_probed: string[]
  competencies_pending: string[]
  started_at: string
  ended_at?: string | null
}

export interface OnboardingResponse {
  interview_id: string
  candidate_highlights: string[]
  total_competencies: number
  first_competency: string
  session_created: boolean
}

export async function checkBackendHealth(): Promise<{ status: string }> {
  return apiFetch<{ status: string }>('/health')
}

export async function createInterviewOnboarding(
  formData: FormData,
): Promise<OnboardingResponse> {
  return apiFetch<OnboardingResponse>('/api/v1/interviews/onboard', {
    method: 'POST',
    body: formData,
  })
}

export async function getInterviewSummary(
  interviewId: string,
): Promise<InterviewSessionSummary> {
  return apiFetch<InterviewSessionSummary>(`/api/v1/interviews/${interviewId}`)
}

export async function createTavusConversation(
  interviewId: string,
  callbackUrl?: string,
): Promise<TavusConversationResponse> {
  const defaultCallback =
    typeof window !== 'undefined'
      ? `${window.location.origin}/api/v1/interviews/tavus-webhook`
      : 'http://localhost:8000/api/v1/interviews/tavus-webhook'

  return apiFetch<TavusConversationResponse>(
    `/api/v1/interviews/${interviewId}/conversation`,
    {
      method: 'POST',
      body: {
        callback_url: callbackUrl || defaultCallback,
      },
    },
  )
}

export async function finalizeInterview(
  interviewId: string,
): Promise<{ interview_id: string; report: Record<string, unknown> }> {
  return apiFetch<{ interview_id: string; report: Record<string, unknown> }>(
    `/api/v1/interviews/${interviewId}/finalize`,
    {
      method: 'POST',
    },
  )
}

export async function getInterviewReport(
  interviewId: string,
): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>(`/api/v1/interviews/${interviewId}/report`)
}

export interface TranscriptTurn {
  speaker: string
  text: string
  timestamp?: number
}

export async function getInterviewTranscript(
  interviewId: string,
): Promise<{ interview_id: string; turns: TranscriptTurn[] }> {
  return apiFetch<{ interview_id: string; turns: TranscriptTurn[] }>(
    `/api/v1/interviews/${interviewId}/transcript`,
  )
}

export async function postInterviewTurn(
  interviewId: string,
  speaker: string,
  text: string,
): Promise<{ interview_id: string; turns: TranscriptTurn[] }> {
  return apiFetch<{ interview_id: string; turns: TranscriptTurn[] }>(
    `/api/v1/interviews/${interviewId}/transcript`,
    {
      method: 'POST',
      body: { speaker, text },
    },
  )
}


