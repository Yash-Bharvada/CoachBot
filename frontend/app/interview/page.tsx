'use client'

import { useEffect, useState, Suspense } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { AnimatePresence, motion } from 'framer-motion'
import {
  AudioLines,
  Camera,
  ChevronDown,
  CircleStop,
  Mic,
  MicOff,
  PhoneOff,
  Video,
  Volume2,
  Sparkles,
} from 'lucide-react'
import { TavusVideoInterview } from '@/components/tavus-video-interview'
import { finalizeInterview, getInterviewTranscript } from '@/lib/api-client'

function InterviewContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const interviewId = searchParams.get('interview_id') || 'demo_session'

  const [mode, setMode] = useState<'video' | 'audio'>('video')
  const [precheck, setPrecheck] = useState(true)
  const [muted, setMuted] = useState(false)
  const [showTranscript, setShowTranscript] = useState(true)
  const [seconds, setSeconds] = useState(0)
  const [ending, setEnding] = useState(false)
  const [finalizing, setFinalizing] = useState(false)
  const [permissionError, setPermissionError] = useState(false)

  const [transcript, setTranscript] = useState<[string, string][]>([])

  useEffect(() => {
    if (precheck) return
    let active = true

    async function pollTranscript() {
      try {
        const res = await getInterviewTranscript(interviewId)
        if (active && res && Array.isArray(res.turns)) {
          const items: [string, string][] = res.turns.map((t) => [
            t.speaker || 'Interviewer',
            t.text || '',
          ])
          setTranscript(items)
        }
      } catch (err) {
        console.warn('Transcript poll error:', err)
      }
    }

    pollTranscript()
    const interval = window.setInterval(pollTranscript, 2500)
    return () => {
      active = false
      window.clearInterval(interval)
    }
  }, [interviewId, precheck])

  useEffect(() => {
    if (precheck) return
    const timer = window.setInterval(() => setSeconds((s) => s + 1), 1000)
    return () => window.clearInterval(timer)
  }, [precheck])

  const time = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(
    seconds % 60,
  ).padStart(2, '0')}`

  async function start() {
    try {
      if (typeof navigator !== 'undefined' && navigator.mediaDevices) {
        await navigator.mediaDevices.getUserMedia({ audio: true, video: true })
      }
      setPrecheck(false)
    } catch (err) {
      console.warn('Media check error:', err)
      // Allow proceeding to session start even if browser permissions are handled inside iframe
      setPrecheck(false)
    }
  }

  async function handleEndAndReview() {
    setFinalizing(true)
    try {
      await finalizeInterview(interviewId)
    } catch (err) {
      console.warn('Finalize error (proceeding to report):', err)
    } finally {
      router.push(`/report?interview_id=${encodeURIComponent(interviewId)}`)
    }
  }

  return (
    <main className="min-h-screen bg-primary p-3 text-primary-foreground sm:p-5">
      <div className="mx-auto flex min-h-[calc(100vh-1.5rem)] max-w-[1500px] flex-col overflow-hidden rounded-[1.5rem] border border-primary-foreground/10 bg-[#142936] shadow-2xl sm:min-h-[calc(100vh-2.5rem)]">
        {/* Header */}
        <header className="flex items-center justify-between border-b border-primary-foreground/10 px-5 py-4 sm:px-7">
          <a href="/" className="flex items-center gap-3 font-semibold">
            <img src="/coachbot-logo.png" alt="CoachBot Logo" className="h-8 w-8 rounded-lg object-contain" />
            <span className="hidden sm:inline font-serif text-lg font-bold">CoachBot</span>
          </a>

          <div className="flex items-center gap-4 text-xs">
            {/* Mode Switcher */}
            <div className="flex items-center rounded-full border border-primary-foreground/15 bg-primary-foreground/5 p-1">
              <button
                type="button"
                onClick={() => setMode('video')}
                className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition ${
                  mode === 'video'
                    ? 'bg-accent text-accent-foreground'
                    : 'text-primary-foreground/70 hover:text-primary-foreground'
                }`}
              >
                <Video className="h-3.5 w-3.5" /> Tavus Video
              </button>
              <button
                type="button"
                onClick={() => setMode('audio')}
                className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition ${
                  mode === 'audio'
                    ? 'bg-accent text-accent-foreground'
                    : 'text-primary-foreground/70 hover:text-primary-foreground'
                }`}
              >
                <AudioLines className="h-3.5 w-3.5" /> Voice Stream
              </button>
            </div>

            <div className="flex items-center gap-3 text-primary-foreground/60">
              <span className="font-mono">{time}</span>
              <span className="flex items-center gap-2">
                <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />
                {precheck ? 'Pre-check' : 'Live'}
              </span>
            </div>
          </div>
        </header>

        {/* Main Content Layout */}
        <div className="grid min-h-0 flex-1 lg:grid-cols-[1fr_340px]">
          {/* Main Stage: Video or Audio Mode */}
          <section className="relative flex min-h-[520px] flex-col justify-between bg-[#1b3440] p-5 sm:p-7">
            {mode === 'video' ? (
              <TavusVideoInterview
                interviewId={interviewId}
                onEndInterview={() => setEnding(true)}
                onFallbackToAudio={() => setMode('audio')}
              />
            ) : (
              <div className="flex flex-1 flex-col justify-between">
                <div className="flex items-center justify-between">
                  <span className="rounded-full border border-primary-foreground/15 bg-primary-foreground/5 px-3 py-1.5 text-xs">
                    Adaptive Voice Interview · Medium
                  </span>
                  <span className="text-xs text-primary-foreground/50">Question 2 of 6</span>
                </div>

                <div className="mx-auto w-full max-w-2xl text-center my-auto">
                  <div className="mx-auto flex aspect-square max-h-56 w-56 items-center justify-center rounded-full border-[14px] border-[#2b5964] bg-[#d6ddd8] shadow-2xl shadow-black/20">
                    <div className="flex h-40 w-40 items-center justify-center rounded-full bg-[#7c9385] font-serif text-6xl text-primary-foreground/80">
                      AI
                    </div>
                  </div>
                  <p className="mt-7 font-serif text-2xl sm:text-3xl leading-snug">
                    Tell me about a technical decision you are proud of.
                  </p>
                  <div className="mt-5 flex justify-center gap-1.5" aria-label="Audio activity">
                    <span className="h-3 w-1 rounded-full bg-accent" />
                    <span className="h-6 w-1 rounded-full bg-accent animate-pulse" />
                    <span className="h-10 w-1 rounded-full bg-accent animate-pulse" />
                    <span className="h-5 w-1 rounded-full bg-accent" />
                    <span className="h-8 w-1 rounded-full bg-accent animate-pulse" />
                    <span className="h-3 w-1 rounded-full bg-accent" />
                  </div>
                </div>

                <div className="flex items-center justify-center gap-3">
                  <button
                    type="button"
                    onClick={() => setMuted(!muted)}
                    aria-label={muted ? 'Unmute microphone' : 'Mute microphone'}
                    className="flex h-12 w-12 items-center justify-center rounded-full border border-primary-foreground/20 bg-primary-foreground/10 hover:bg-primary-foreground/20 transition"
                  >
                    {muted ? <MicOff className="h-5 w-5" /> : <Mic className="h-5 w-5" />}
                  </button>
                  <button
                    type="button"
                    onClick={() => setEnding(true)}
                    aria-label="End interview"
                    className="flex h-12 items-center gap-2 rounded-full bg-[#a85447] px-6 text-sm font-medium hover:bg-[#bd6355] transition"
                  >
                    <PhoneOff className="h-4 w-4" /> End interview
                  </button>
                </div>
              </div>
            )}
          </section>

          {/* Sidebar: Live Transcript & Details */}
          <aside className="border-t border-primary-foreground/10 bg-[#10232d] p-5 lg:border-l lg:border-t-0 flex flex-col justify-between">
            <div>
              <button
                type="button"
                onClick={() => setShowTranscript(!showTranscript)}
                className="flex w-full items-center justify-between text-left"
              >
                <span className="text-xs font-semibold uppercase tracking-[0.16em] text-primary-foreground/60">
                  Live Transcript
                </span>
                <ChevronDown
                  className={`h-4 w-4 transition-transform ${
                    showTranscript ? 'rotate-180' : ''
                  }`}
                />
              </button>

              <AnimatePresence>
                {showTranscript && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    exit={{ opacity: 0, height: 0 }}
                    className="overflow-hidden"
                  >
                    <div className="mt-5 space-y-4">
                      {transcript.length > 0 ? (
                        transcript.map(([speaker, text], idx) => (
                          <div key={idx} className="rounded-lg bg-primary-foreground/5 p-3">
                            <p
                              className={`text-xs font-semibold ${
                                speaker === 'You' ? 'text-accent' : 'text-primary-foreground/70'
                              }`}
                            >
                              {speaker}
                            </p>
                            <p className="mt-1 text-xs leading-5 text-primary-foreground/90">
                              {text}
                            </p>
                          </div>
                        ))
                      ) : (
                        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-primary-foreground/15 p-6 text-center text-xs text-primary-foreground/50">
                          <AudioLines className="h-6 w-6 animate-pulse text-accent/70" />
                          <p className="mt-2 font-medium text-primary-foreground/80">Listening for conversation...</p>
                          <p className="mt-1 text-[11px] leading-4 text-primary-foreground/50">
                            Spoken turns will appear here in real time as you talk with your interviewer.
                          </p>
                        </div>
                      )}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            <div className="mt-6 rounded-xl border border-primary-foreground/10 bg-primary-foreground/5 p-4">
              <div className="flex items-center gap-2 text-xs text-accent font-medium">
                <Sparkles className="h-4 w-4" /> AI Coaching Active
              </div>
              <p className="mt-2 text-xs leading-5 text-primary-foreground/60">
                Tavus AI evaluates your communication clarity, technical depth, and confidence in real time.
              </p>
            </div>
          </aside>
        </div>

        {/* Precheck Overlay */}
        <AnimatePresence>
          {precheck && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="absolute inset-0 z-10 flex items-center justify-center bg-primary/75 p-5 backdrop-blur-md"
            >
              <div className="w-full max-w-md rounded-2xl border border-border bg-card p-7 text-foreground shadow-2xl">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-full bg-accent/15 text-accent">
                    <Camera className="h-5 w-5" />
                  </div>
                  <div>
                    <p className="eyebrow">Before we begin</p>
                    <h1 className="mt-1 font-serif text-2xl">Check your camera & mic</h1>
                  </div>
                </div>
                <p className="mt-5 text-sm leading-6 text-muted-foreground">
                  Allow camera and microphone access so Tavus AI can render your video avatar session and stream audio.
                </p>
                {permissionError && (
                  <p role="alert" className="mt-4 text-sm text-destructive">
                    Permissions were blocked. Please allow media permissions in your browser.
                  </p>
                )}
                <button
                  type="button"
                  onClick={start}
                  className="mt-6 inline-flex h-12 w-full items-center justify-center gap-2 rounded-full bg-primary font-medium text-primary-foreground transition hover:opacity-90"
                >
                  I’m ready →
                </button>
                <button
                  type="button"
                  onClick={() => router.push('/')}
                  className="mt-3 w-full py-2 text-sm text-muted-foreground hover:text-foreground"
                >
                  Return to Home
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* End Interview Overlay */}
        <AnimatePresence>
          {ending && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="fixed inset-0 z-20 flex items-center justify-center bg-primary/60 p-5 backdrop-blur-sm"
            >
              <div className="w-full max-w-sm rounded-2xl bg-card p-7 text-foreground shadow-2xl">
                <CircleStop className="h-7 w-7 text-accent" />
                <h2 className="mt-4 font-serif text-2xl">End this interview?</h2>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">
                  Your session data will be saved and an AI feedback report will be generated.
                </p>
                <div className="mt-6 flex gap-3">
                  <button
                    type="button"
                    onClick={() => setEnding(false)}
                    disabled={finalizing}
                    className="h-11 flex-1 rounded-full border border-border text-sm transition hover:bg-muted"
                  >
                    Keep going
                  </button>
                  <button
                    type="button"
                    onClick={handleEndAndReview}
                    disabled={finalizing}
                    className="h-11 flex-1 rounded-full bg-primary text-sm text-primary-foreground transition hover:opacity-90"
                  >
                    {finalizing ? 'Finalizing...' : 'End & Review'}
                  </button>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </main>
  )
}

export default function InterviewPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-primary p-5 text-white">Loading interview...</div>}>
      <InterviewContent />
    </Suspense>
  )
}
