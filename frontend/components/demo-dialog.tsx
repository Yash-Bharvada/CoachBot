'use client'

import { useRef, useEffect } from 'react'
import Link from 'next/link'
import { X, Sparkles, ArrowRight, ShieldCheck, Cpu, FileText, CheckCircle2 } from 'lucide-react'

type Props = { open: boolean; onOpenChange: (open: boolean) => void }

export function DemoDialog({ open, onOpenChange }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    if (open && videoRef.current) {
      videoRef.current.currentTime = 0
      videoRef.current.play().catch(() => {})
    }
  }, [open])

  if (!open) return null

  const close = () => {
    if (videoRef.current) {
      videoRef.current.pause()
    }
    onOpenChange(false)
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-md animate-fade-in"
      role="dialog"
      aria-modal="true"
      aria-labelledby="demo-title"
      onClick={close}
    >
      <div
        className="relative flex h-[min(820px,calc(100vh-2rem))] w-full max-w-5xl flex-col overflow-hidden rounded-3xl border border-border/80 bg-card shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-6 py-4 bg-muted/40">
          <div>
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-accent">
              <Sparkles className="h-3.5 w-3.5" /> Complete AI Interview Flow
            </div>
            <h2 id="demo-title" className="font-serif text-2xl font-bold tracking-tight text-foreground">
              How the AI Interview Works
            </h2>
          </div>
          <button
            type="button"
            onClick={close}
            aria-label="Close demo"
            className="rounded-full p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Main Content Area - Video Player */}
        <div className="relative flex min-h-0 flex-1 flex-col bg-slate-950">
          <div className="relative flex-1 w-full h-full flex items-center justify-center bg-black">
            <video
              ref={videoRef}
              src="/demo-video.mp4"
              controls
              autoPlay
              playsInline
              preload="auto"
              className="h-full w-full max-h-full object-contain"
            />
          </div>

          {/* Workflow Steps Bar */}
          <div className="border-t border-white/10 bg-slate-900/90 px-6 py-4 backdrop-blur">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div className="flex items-start gap-3 rounded-xl bg-white/5 p-3 text-xs text-white/90 border border-white/5">
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-accent/20 text-accent">
                  <FileText className="h-4 w-4" />
                </div>
                <div>
                  <p className="font-semibold text-white">1. Grounding</p>
                  <p className="text-white/60 text-[11px] mt-0.5">Resume & Job Description context ingestion</p>
                </div>
              </div>

              <div className="flex items-start gap-3 rounded-xl bg-white/5 p-3 text-xs text-white/90 border border-white/5">
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-accent/20 text-accent">
                  <Cpu className="h-4 w-4" />
                </div>
                <div>
                  <p className="font-semibold text-white">2. Adaptive Flow</p>
                  <p className="text-white/60 text-[11px] mt-0.5">Real-time voice & video AI interviewer dialogue</p>
                </div>
              </div>

              <div className="flex items-start gap-3 rounded-xl bg-white/5 p-3 text-xs text-white/90 border border-white/5">
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-accent/20 text-accent">
                  <ShieldCheck className="h-4 w-4" />
                </div>
                <div>
                  <p className="font-semibold text-white">3. Actionable Report</p>
                  <p className="text-white/60 text-[11px] mt-0.5">Detailed confidence, accuracy & feedback breakdown</p>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center justify-between border-t border-border px-6 py-4 bg-card">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <CheckCircle2 className="h-4 w-4 text-accent" />
            <span>Ready to experience your personalized AI mock interview?</span>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={close}
              className="rounded-full border border-border px-4 py-2 text-xs font-medium transition-colors hover:bg-muted"
            >
              Close Video
            </button>
            <Link
              href="/onboarding"
              onClick={close}
              className="inline-flex items-center gap-2 rounded-full bg-primary px-5 py-2 text-xs font-semibold text-primary-foreground transition-transform hover:scale-105"
            >
              Start Free Interview <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
        </div>
      </div>
    </div>
  )
}

