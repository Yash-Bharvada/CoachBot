'use client'

import { useEffect, useState, useRef, Suspense } from 'react'
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
  Sparkles,
  Send,
  RefreshCw,
  Bot,
  User,
  Radio,
} from 'lucide-react'
import { TavusVideoInterview } from '@/components/tavus-video-interview'
import { finalizeInterview, getInterviewTranscript, postInterviewTurn } from '@/lib/api-client'

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

  // Real-time transcript state
  const [transcript, setTranscript] = useState<[string, string][]>([])
  const [interimText, setInterimText] = useState<string>('')
  const [isListening, setIsListening] = useState<boolean>(false)
  const [isInterviewerThinking, setIsInterviewerThinking] = useState<boolean>(false)
  const [manualInput, setManualInput] = useState<string>('')

  const recognitionRef = useRef<any>(null)
  const transcriptScrollRef = useRef<HTMLDivElement>(null)
  const isMountedRef = useRef<boolean>(true)

  // Auto-scroll transcript container to bottom when turns update or interim text streams
  useEffect(() => {
    if (transcriptScrollRef.current) {
      transcriptScrollRef.current.scrollTop = transcriptScrollRef.current.scrollHeight
    }
  }, [transcript, interimText, isInterviewerThinking])

  // Polling backend transcript sync
  useEffect(() => {
    if (precheck) return
    isMountedRef.current = true

    async function pollTranscript() {
      try {
        const res = await getInterviewTranscript(interviewId)
        if (isMountedRef.current && res && Array.isArray(res.turns)) {
          const items: [string, string][] = res.turns.map((t) => [
            t.speaker || 'Interviewer',
            t.text || '',
          ])
          // Only update if transcript items changed
          setTranscript((prev) => {
            if (JSON.stringify(prev) !== JSON.stringify(items)) {
              return items
            }
            return prev
          })
        }
      } catch (err) {
        console.warn('Transcript poll error:', err)
      }
    }

    pollTranscript()
    const interval = window.setInterval(pollTranscript, 2000)
    return () => {
      isMountedRef.current = false
      window.clearInterval(interval)
    }
  }, [interviewId, precheck])

  // Timer counter
  useEffect(() => {
    if (precheck) return
    const timer = window.setInterval(() => setSeconds((s) => s + 1), 1000)
    return () => window.clearInterval(timer)
  }, [precheck])

  const time = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(
    seconds % 60,
  ).padStart(2, '0')}`

  // Continuous speech recognition with auto-restart and interim live streaming
  useEffect(() => {
    if (precheck || muted) {
      if (recognitionRef.current) {
        try {
          recognitionRef.current.stop()
        } catch (_) {}
      }
      setIsListening(false)
      setInterimText('')
      return
    }

    if (typeof window === 'undefined') return
    const windowObj = window as unknown as Record<string, any>
    const SpeechRecognitionClass = windowObj.SpeechRecognition || windowObj.webkitSpeechRecognition

    if (!SpeechRecognitionClass) {
      console.warn('Web Speech API is not supported in this browser.')
      return
    }

    let shouldRestart = true

    try {
      const recognition = new SpeechRecognitionClass()
      recognition.continuous = true
      recognition.interimResults = true
      recognition.lang = 'en-US'
      recognitionRef.current = recognition

      recognition.onstart = () => {
        setIsListening(true)
      }

      recognition.onresult = (event: any) => {
        let currentInterim = ''
        for (let i = event.resultIndex; i < event.results.length; ++i) {
          const trans = event.results[i][0]?.transcript || ''
          if (event.results[i].isFinal) {
            const finalText = trans.trim()
            if (finalText.length >= 2) {
              // Optimistically append candidate turn locally
              setTranscript((prev) => [...prev, ['You', finalText]])
              setInterimText('')
              setIsInterviewerThinking(true)

              // Dispatch turn to backend to evaluate & generate interviewer follow-up
              postInterviewTurn(interviewId, 'You', finalText)
                .then((res) => {
                  if (res && Array.isArray(res.turns)) {
                    setTranscript(res.turns.map((t) => [t.speaker || 'Interviewer', t.text || '']))
                  }
                })
                .catch((err) => console.warn('Post turn error:', err))
                .finally(() => setIsInterviewerThinking(false))
            }
          } else {
            currentInterim += trans
          }
        }
        setInterimText(currentInterim.trim())
      }

      recognition.onerror = (err: any) => {
        if (err.error !== 'no-speech' && err.error !== 'aborted') {
          console.warn('Speech recognition warning:', err.error)
        }
      }

      recognition.onend = () => {
        setIsListening(false)
        if (shouldRestart && !muted && !precheck && isMountedRef.current) {
          try {
            recognition.start()
          } catch (_) {}
        }
      }

      recognition.start()
    } catch (e) {
      console.warn('Speech recognition start failed:', e)
    }

    return () => {
      shouldRestart = false
      if (recognitionRef.current) {
        try {
          recognitionRef.current.stop()
        } catch (_) {}
      }
    }
  }, [interviewId, precheck, muted])

  async function handleSendManualTurn(e?: React.FormEvent) {
    if (e) e.preventDefault()
    const text = manualInput.trim()
    if (!text) return

    setManualInput('')
    setTranscript((prev) => [...prev, ['You', text]])
    setIsInterviewerThinking(true)

    try {
      const res = await postInterviewTurn(interviewId, 'You', text)
      if (res && Array.isArray(res.turns)) {
        setTranscript(res.turns.map((t) => [t.speaker || 'Interviewer', t.text || '']))
      }
    } catch (err) {
      console.warn('Manual turn submit error:', err)
    } finally {
      setIsInterviewerThinking(false)
    }
  }

  async function start() {
    try {
      if (typeof navigator !== 'undefined' && navigator.mediaDevices) {
        await navigator.mediaDevices.getUserMedia({ audio: true, video: true })
      }
      setPrecheck(false)
    } catch (err) {
      console.warn('Media check error:', err)
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
      <div className="mx-auto flex min-h-[calc(100vh-1.5rem)] max-w-[1550px] flex-col overflow-hidden rounded-[1.5rem] border border-primary-foreground/10 bg-[#142936] shadow-2xl sm:min-h-[calc(100vh-2.5rem)]">
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
        <div className="grid min-h-0 flex-1 lg:grid-cols-[1fr_390px]">
          {/* Main Stage: Video or Audio Mode */}
          <section className="relative flex min-h-[520px] flex-col justify-between bg-[#1b3440] p-4 sm:p-6">
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
                    Adaptive Voice Interview · Live
                  </span>
                  <span className="text-xs text-primary-foreground/50">
                    Turns completed: {transcript.filter((t) => t[0] === 'You').length}
                  </span>
                </div>

                <div className="mx-auto w-full max-w-2xl text-center my-auto">
                  <div className="mx-auto flex aspect-square max-h-52 w-52 items-center justify-center rounded-full border-[12px] border-[#2b5964] bg-[#d6ddd8] shadow-2xl shadow-black/20">
                    <div className="flex h-36 w-36 items-center justify-center rounded-full bg-[#7c9385] font-serif text-5xl text-primary-foreground/80">
                      AI
                    </div>
                  </div>
                  <p className="mt-6 font-serif text-xl sm:text-2xl leading-snug px-4">
                    {transcript.length > 0
                      ? transcript[transcript.length - 1][1]
                      : 'Welcome! Tell me about a technical project you are proud of.'}
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

                <div className="flex items-center justify-center gap-3 pt-4">
                  <button
                    type="button"
                    onClick={() => setMuted(!muted)}
                    aria-label={muted ? 'Unmute microphone' : 'Mute microphone'}
                    className={`flex h-12 w-12 items-center justify-center rounded-full border transition ${
                      muted
                        ? 'border-destructive/40 bg-destructive/20 text-destructive'
                        : 'border-primary-foreground/20 bg-primary-foreground/10 hover:bg-primary-foreground/20'
                    }`}
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

          {/* Sidebar: Real-Time Live Transcript */}
          <aside className="flex flex-col justify-between border-t border-primary-foreground/10 bg-[#10232d] lg:border-l lg:border-t-0">
            {/* Sidebar Header & Mic Status */}
            <div className="border-b border-primary-foreground/10 p-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="relative flex h-2.5 w-2.5">
                    {isListening && !muted ? (
                      <>
                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75"></span>
                        <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500"></span>
                      </>
                    ) : (
                      <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-500"></span>
                    )}
                  </span>
                  <span className="text-xs font-semibold uppercase tracking-[0.16em] text-primary-foreground/80">
                    Live Transcript
                  </span>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setMuted(!muted)}
                    className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium transition ${
                      isListening && !muted
                        ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                        : 'bg-primary-foreground/10 text-primary-foreground/60 border border-primary-foreground/10'
                    }`}
                  >
                    {isListening && !muted ? (
                      <>
                        <Radio className="h-3 w-3 animate-pulse" /> Mic Active
                      </>
                    ) : (
                      <>
                        <MicOff className="h-3 w-3" /> Mic Muted
                      </>
                    )}
                  </button>
                </div>
              </div>
            </div>

            {/* Transcript Messages List */}
            <div
              ref={transcriptScrollRef}
              className="flex-1 overflow-y-auto p-4 space-y-3.5 max-h-[calc(100vh-280px)]"
            >
              {transcript.length > 0 ? (
                transcript.map(([speaker, text], idx) => {
                  const isUser = speaker.toLowerCase() === 'you' || speaker.toLowerCase() === 'candidate'
                  return (
                    <motion.div
                      key={idx}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      className={`flex flex-col rounded-xl p-3.5 text-xs transition ${
                        isUser
                          ? 'border border-accent/20 bg-accent/10 ml-3'
                          : 'border border-primary-foreground/10 bg-[#162c38] mr-3'
                      }`}
                    >
                      <div className="flex items-center gap-1.5 pb-1 font-semibold">
                        {isUser ? (
                          <>
                            <User className="h-3.5 w-3.5 text-accent" />
                            <span className="text-accent">You (Candidate)</span>
                          </>
                        ) : (
                          <>
                            <Bot className="h-3.5 w-3.5 text-cyan-400" />
                            <span className="text-cyan-400">Interviewer (AI)</span>
                          </>
                        )}
                      </div>
                      <p className="mt-0.5 text-xs leading-relaxed text-primary-foreground/90 whitespace-pre-wrap">
                        {text}
                      </p>
                    </motion.div>
                  )
                })
              ) : (
                <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-primary-foreground/15 p-8 text-center text-xs text-primary-foreground/50">
                  <AudioLines className="h-7 w-7 animate-pulse text-accent/70" />
                  <p className="mt-3 font-medium text-primary-foreground/90">
                    Listening for conversation...
                  </p>
                  <p className="mt-1 text-[11px] leading-4 text-primary-foreground/50">
                    Speak into your microphone or type below. Both candidate answers and interviewer questions will stream live here.
                  </p>
                </div>
              )}

              {/* Real-time interim streaming speech preview */}
              {interimText && (
                <motion.div
                  initial={{ opacity: 0, scale: 0.98 }}
                  animate={{ opacity: 1, scale: 1 }}
                  className="rounded-xl border border-dashed border-accent/40 bg-accent/15 p-3.5 text-xs ml-3"
                >
                  <div className="flex items-center gap-2 font-semibold text-accent pb-1">
                    <span className="flex h-2 w-2 relative">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-75"></span>
                      <span className="relative inline-flex rounded-full h-2 w-2 bg-accent"></span>
                    </span>
                    <span>You (speaking live...)</span>
                  </div>
                  <p className="mt-0.5 text-xs italic text-primary-foreground/95">
                    {interimText}
                  </p>
                </motion.div>
              )}

              {/* Interviewer Thinking Indicator */}
              {isInterviewerThinking && (
                <div className="flex items-center gap-2 rounded-xl border border-cyan-500/20 bg-cyan-500/10 p-3 text-xs text-cyan-300 mr-3">
                  <Bot className="h-3.5 w-3.5 animate-spin text-cyan-400" />
                  <span className="italic">Interviewer is formulating the next question...</span>
                </div>
              )}
            </div>

            {/* Bottom Transcript Controls & Text Composer */}
            <div className="border-t border-primary-foreground/10 bg-[#142936] p-3.5">
              <form onSubmit={handleSendManualTurn} className="flex items-center gap-2">
                <input
                  type="text"
                  value={manualInput}
                  onChange={(e) => setManualInput(e.target.value)}
                  placeholder="Type an answer or clarification..."
                  className="h-10 flex-1 rounded-full border border-primary-foreground/15 bg-primary-foreground/5 px-4 text-xs text-primary-foreground placeholder:text-primary-foreground/40 focus:border-accent focus:outline-none"
                />
                <button
                  type="submit"
                  disabled={!manualInput.trim()}
                  className="flex h-10 w-10 items-center justify-center rounded-full bg-accent text-accent-foreground transition hover:opacity-90 disabled:opacity-30"
                  aria-label="Send response"
                >
                  <Send className="h-4 w-4" />
                </button>
              </form>

              <div className="mt-2.5 flex items-center justify-between text-[11px] text-primary-foreground/50">
                <span className="flex items-center gap-1 text-accent">
                  <Sparkles className="h-3 w-3" /> Adaptive LLM Judge Active
                </span>
                <span>{transcript.length} turns recorded</span>
              </div>
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
                  Allow camera and microphone access so Tavus AI can render your video avatar session and transcribe your answers in real time.
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
                  Your session data and full transcript will be saved and an AI feedback report will be generated.
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
