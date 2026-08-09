'use client'

import { useEffect, useState } from 'react'
import {
  AlertCircle,
  Camera,
  CheckCircle2,
  Loader2,
  Mic,
  PhoneOff,
  RefreshCw,
  Video,
  Volume2,
} from 'lucide-react'
import { createTavusConversation, postInterviewTurn, TavusConversationResponse } from '@/lib/api-client'

interface TavusVideoInterviewProps {
  interviewId: string
  onEndInterview?: () => void
  onFallbackToAudio?: () => void
}

export function TavusVideoInterview({
  interviewId,
  onEndInterview,
  onFallbackToAudio,
}: TavusVideoInterviewProps) {
  const [loading, setLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)
  const [tavusData, setTavusData] = useState<TavusConversationResponse | null>(null)
  const [iframeLoaded, setIframeLoaded] = useState<boolean>(false)

  async function initializeSession() {
    setLoading(true)
    setError(null)
    setIframeLoaded(false)

    try {
      const data = await createTavusConversation(interviewId)
      setTavusData(data)
    } catch (err: unknown) {
      console.error('Failed to initialize Tavus conversation:', err)
      const message =
        err instanceof Error ? err.message : 'Unable to connect to Tavus AI Video Service.'
      setError(message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (interviewId) {
      initializeSession()
    }
  }, [interviewId])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const windowObj = window as unknown as Record<string, any>
    const SpeechRecognitionClass = windowObj.SpeechRecognition || windowObj.webkitSpeechRecognition

    if (!SpeechRecognitionClass) return

    let recognition: any = null
    try {
      recognition = new SpeechRecognitionClass()
      recognition.continuous = true
      recognition.interimResults = false
      recognition.lang = 'en-US'

      recognition.onresult = (event: any) => {
        for (let i = event.resultIndex; i < event.results.length; ++i) {
          if (event.results[i].isFinal) {
            const text = event.results[i][0].transcript.trim()
            if (text && text.length > 2) {
              postInterviewTurn(interviewId, 'You', text).catch((err) =>
                console.warn('Post turn error:', err),
              )
            }
          }
        }
      }

      recognition.onerror = (err: any) => {
        if (err.error !== 'no-speech' && err.error !== 'aborted') {
          console.warn('Speech recognition error:', err.error)
        }
      }

      recognition.start()
    } catch (e) {
      console.warn('Speech recognition start failed:', e)
    }

    return () => {
      if (recognition) {
        try {
          recognition.stop()
        } catch (_) {}
      }
    }
  }, [interviewId])

  if (loading) {
    return (
      <div className="flex min-h-[480px] w-full flex-col items-center justify-center rounded-2xl border border-primary-foreground/10 bg-[#1b3440] p-8 text-center text-primary-foreground">
        <div className="relative flex h-16 w-16 items-center justify-center rounded-full bg-accent/20 text-accent">
          <Loader2 className="h-8 w-8 animate-spin" />
        </div>
        <h3 className="mt-6 font-serif text-xl font-medium sm:text-2xl">
          Initializing Tavus AI Interviewer...
        </h3>
        <p className="mt-2 max-w-md text-sm leading-6 text-primary-foreground/60">
          Setting up high-definition video avatar, loading role context, and securing media streams.
        </p>
      </div>
    )
  }

  if (error || !tavusData) {
    return (
      <div className="flex min-h-[480px] w-full flex-col items-center justify-center rounded-2xl border border-destructive/20 bg-[#1b3440] p-8 text-center text-primary-foreground">
        <div className="flex h-16 w-16 items-center justify-center rounded-full bg-destructive/20 text-destructive">
          <AlertCircle className="h-8 w-8" />
        </div>
        <h3 className="mt-5 font-serif text-xl font-medium sm:text-2xl">
          Tavus AI Connection Failed
        </h3>
        <p className="mt-2 max-w-md text-sm leading-6 text-primary-foreground/70">
          {error || 'Unable to establish video conversation session with Tavus.'}
        </p>

        <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
          <a
            href="/onboarding"
            className="inline-flex h-11 items-center gap-2 rounded-full bg-primary px-6 text-sm font-medium text-primary-foreground transition hover:opacity-90"
          >
            Start Onboarding →
          </a>
          <button
            type="button"
            onClick={initializeSession}
            className="inline-flex h-11 items-center gap-2 rounded-full bg-accent px-6 text-sm font-medium text-accent-foreground transition hover:opacity-90"
          >
            <RefreshCw className="h-4 w-4" /> Retry Connection
          </button>
          {onFallbackToAudio && (
            <button
              type="button"
              onClick={onFallbackToAudio}
              className="inline-flex h-11 items-center gap-2 rounded-full border border-primary-foreground/20 bg-primary-foreground/10 px-6 text-sm font-medium text-primary-foreground transition hover:bg-primary-foreground/20"
            >
              Switch to Voice Mode
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="relative flex min-h-[550px] w-full flex-col overflow-hidden rounded-2xl border border-primary-foreground/15 bg-[#10232d]">
      {/* Status Bar */}
      <div className="flex items-center justify-between border-b border-primary-foreground/10 bg-[#142936] px-5 py-3 text-xs text-primary-foreground/70">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75"></span>
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500"></span>
          </span>
          <span className="font-medium text-primary-foreground">Tavus AI Video Session</span>
          <span className="text-primary-foreground/40">|</span>
          <span className="font-mono text-primary-foreground/60">ID: {tavusData.conversation_id.slice(0, 12)}...</span>
        </div>

        <div className="flex items-center gap-3 text-xs">
          <span className="inline-flex items-center gap-1 text-emerald-400">
            <CheckCircle2 className="h-3.5 w-3.5" /> Media Active
          </span>
        </div>
      </div>

      {/* Video Container */}
      <div className="relative flex-1 bg-black">
        {!iframeLoaded && (
          <div className="absolute inset-0 flex items-center justify-center bg-[#1b3440] text-primary-foreground">
            <div className="flex flex-col items-center gap-3">
              <Loader2 className="h-8 w-8 animate-spin text-accent" />
              <p className="text-sm font-medium text-primary-foreground/70">
                Loading Tavus Video Stream...
              </p>
            </div>
          </div>
        )}

        <iframe
          src={tavusData.conversation_url}
          allow="camera; microphone; display-capture; autoplay; encrypted-media; fullscreen"
          onLoad={() => setIframeLoaded(true)}
          className="h-full w-full border-0 min-h-[500px]"
          title="Tavus AI Mock Interviewer Video Stream"
        />
      </div>

      {/* Control Toolbar */}
      <div className="flex items-center justify-between border-t border-primary-foreground/10 bg-[#142936] px-5 py-3">
        <div className="flex items-center gap-2 text-xs text-primary-foreground/60">
          <Video className="h-4 w-4 text-accent" /> Real-time Video Stream
        </div>

        <div className="flex items-center gap-3">
          {onEndInterview && (
            <button
              type="button"
              onClick={onEndInterview}
              className="inline-flex h-10 items-center gap-2 rounded-full bg-destructive px-5 text-xs font-medium text-destructive-foreground transition hover:opacity-90"
            >
              <PhoneOff className="h-3.5 w-3.5" /> End Video Interview
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
