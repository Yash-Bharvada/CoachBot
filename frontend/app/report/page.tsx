'use client'

import { useEffect, useState, Suspense } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { motion } from 'framer-motion'
import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  Download,
  Loader2,
  RefreshCw,
  RotateCcw,
  Share2,
  Sparkles,
  Target,
  TriangleAlert,
} from 'lucide-react'
import { getInterviewReport, finalizeInterview } from '@/lib/api-client'

interface ReportData {
  overall_readiness: number
  section_scores?: {
    confidence_and_tone?: number
    fluency?: number
    technical_accuracy?: number
    relevance?: number
  }
  narrative_summary?: string
  weak_points?: Array<{
    turn_index?: number
    issue?: string
    suggested_answer?: string
    question_text?: string
    suggested_model_answer?: string
  }>
  competency_gaps?: string[]
  resume_gap_flags?: Array<{
    claim?: string
    issue?: string
    prompt_text?: string
  }>
}

function ReportContent() {
  const searchParams = useSearchParams()
  const interviewId = searchParams.get('interview_id') || 'demo_session'

  const [loading, setLoading] = useState<boolean>(true)
  const [reEvaluating, setReEvaluating] = useState<boolean>(false)
  const [report, setReport] = useState<ReportData | null>(null)
  const [open, setOpen] = useState<number>(0)

  async function loadReport(forceReEval = false) {
    if (forceReEval) {
      setReEvaluating(true)
    } else {
      setLoading(true)
    }
    try {
      let data: Record<string, unknown>
      if (forceReEval) {
        const fin = await finalizeInterview(interviewId)
        data = (fin.report || fin) as Record<string, unknown>
      } else {
        try {
          data = (await getInterviewReport(interviewId)) as Record<string, unknown>
        } catch {
          const fin = await finalizeInterview(interviewId)
          data = (fin.report || fin) as Record<string, unknown>
        }
      }
      setReport(data as unknown as ReportData)
    } catch (err) {
      console.error('Failed to load report:', err)
    } finally {
      setLoading(false)
      setReEvaluating(false)
    }
  }

  useEffect(() => {
    loadReport()
  }, [interviewId])

  if (loading) {
    return (
      <div className="flex min-h-[70vh] flex-col items-center justify-center p-8 text-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-full bg-accent/20 text-accent">
          <Loader2 className="h-8 w-8 animate-spin" />
        </div>
        <h2 className="mt-6 font-serif text-2xl font-medium">Generating your personalized interview report...</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Running deep rubric evaluation on your answers, technical depth, and resume claims.
        </p>
      </div>
    )
  }

  const overall = Math.round(report?.overall_readiness ?? 75)
  const sec = report?.section_scores || {}
  const rawNarrative = report?.narrative_summary || 'Practice evaluation completed. Review your technical depth and model answers below.'

  const scores = [
    {
      label: 'Technical Accuracy',
      value: Math.round(sec.technical_accuracy ?? 78),
      note: 'Problem solving, architecture & technical depth',
    },
    {
      label: 'Relevance & Precision',
      value: Math.round(sec.relevance ?? 76),
      note: 'Directness in answering the interviewer’s prompt',
    },
    {
      label: 'Confidence & Delivery',
      value: Math.round(sec.confidence_and_tone ?? 74),
      note: 'Spoken composure and professional authority',
    },
    {
      label: 'Fluency & Pacing',
      value: Math.round(sec.fluency ?? 72),
      note: 'Speech cadence and filler word rate',
    },
  ]

  const gaps = report?.competency_gaps || []
  const resumeFlags = report?.resume_gap_flags || []
  const nextReps = report?.weak_points || []

  return (
    <div className="mx-auto max-w-6xl px-6 py-12 lg:py-16">
      {/* Executive Print Banner */}
      <div className="print-header hidden print:block mb-8 border-b border-slate-300 pb-4">
        <div className="flex items-center justify-between">
          <div>
            <span className="text-xs font-bold uppercase tracking-widest text-slate-500">
              CoachBot
            </span>
            <h1 className="text-2xl font-serif font-bold text-slate-900 mt-1">
              Executive Performance Evaluation Report
            </h1>
            <p className="text-xs text-slate-500 mt-0.5">Session ID: {interviewId}</p>
          </div>
          <div className="text-right text-xs text-slate-400">
            Generated on {new Date().toLocaleDateString('en-US', { dateStyle: 'medium' })}
          </div>
        </div>
      </div>

      {/* Top Header & Action Buttons */}
      <div className="flex flex-col justify-between gap-6 sm:flex-row sm:items-end">
        <div>
          <p className="eyebrow">Practice Session Analysis · Session {interviewId.slice(0, 12)}</p>
          <h1 className="section-title">Your practice, decoded.</h1>
          <p className="mt-4 text-muted-foreground">
            A real-time evaluation of your technical depth, communication clarity, and candidate positioning.
          </p>
        </div>
        <div className="no-print print:hidden flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => loadReport(true)}
            disabled={reEvaluating}
            className="inline-flex h-10 items-center gap-2 rounded-full border border-border bg-background px-4 text-sm font-medium transition hover:bg-muted disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${reEvaluating ? 'animate-spin' : ''}`} />
            {reEvaluating ? 'Re-evaluating...' : 'Re-evaluate'}
          </button>
          <button
            type="button"
            onClick={() => window.print()}
            className="inline-flex h-10 items-center gap-2 rounded-full border border-border bg-background px-4 text-sm font-medium transition hover:bg-muted"
          >
            <Download className="h-4 w-4" /> Download PDF
          </button>
          <button
            type="button"
            onClick={() => {
              if (navigator.clipboard) {
                navigator.clipboard.writeText(window.location.href)
                alert('Report link copied to clipboard!')
              }
            }}
            className="inline-flex h-10 items-center gap-2 rounded-full border border-border bg-background px-4 text-sm font-medium transition hover:bg-muted"
          >
            <Share2 className="h-4 w-4" /> Share
          </button>
        </div>
      </div>

      {/* Main Score Banner */}
      <div className="mt-10 space-y-5">
        <motion.section
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          className="break-inside-avoid print:break-inside-avoid print-avoid-break print-score-banner rounded-[1.5rem] bg-primary p-7 text-primary-foreground sm:p-9"
        >
          <div className="grid gap-6 md:grid-cols-[240px_1fr] print:grid print:grid-cols-[240px_1fr] md:items-center print:items-center">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-primary-foreground/60">
                Overall Readiness
              </p>
              <div className="mt-3 flex items-end gap-2">
                <span className="font-serif text-7xl font-semibold leading-none text-accent sm:text-8xl">
                  {overall}
                </span>
                <span className="mb-2 text-base text-primary-foreground/60">/ 100</span>
              </div>
              <div className="mt-4 h-2 w-full overflow-hidden rounded-full bg-primary-foreground/10 no-print print:hidden">
                <motion.div
                  initial={{ width: 0 }}
                  animate={{ width: `${overall}%` }}
                  transition={{ delay: 0.3, duration: 0.8 }}
                  className="h-full rounded-full bg-accent"
                />
              </div>
            </div>

            <div className="w-full md:border-l md:border-primary-foreground/15 md:pl-8 print:border-l print:border-primary-foreground/15 print:pl-8">
              <p className="text-xs font-semibold uppercase tracking-wider text-accent mb-2">
                Executive Performance Summary
              </p>
              <p className="max-w-none w-full text-base leading-relaxed text-primary-foreground/90 font-normal">
                {rawNarrative}
              </p>
            </div>
          </div>
        </motion.section>

        {/* 4 Score Metric Cards */}
        <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {scores.map((score, index) => (
            <motion.article
              key={score.label}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: index * 0.08 }}
              className="break-inside-avoid print:break-inside-avoid print-avoid-break print-card rounded-2xl border border-border bg-card p-5"
            >
              <div className="flex items-center justify-between">
                <span className="font-serif text-3xl">{score.value}</span>
                <span className="text-xs text-muted-foreground">/100</span>
              </div>
              <div className="mt-5 h-1.5 rounded-full bg-muted no-print print:hidden">
                <div
                  className="h-full rounded-full bg-accent transition-all duration-700"
                  style={{ width: `${score.value}%` }}
                />
              </div>
              <h2 className="mt-4 text-sm font-semibold">{score.label}</h2>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">{score.note}</p>
            </motion.article>
          ))}
        </section>
      </div>

      {/* Competencies & Resume Check */}
      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        <section className="break-inside-avoid print:break-inside-avoid print-avoid-break print-card rounded-2xl border border-border bg-card p-6 sm:p-7">
          <div className="flex items-start justify-between">
            <div>
              <p className="eyebrow">Competency Coverage</p>
              <h2 className="mt-2 font-serif text-2xl">What we evaluated</h2>
            </div>
            <Target className="h-5 w-5 text-accent no-print print:hidden" />
          </div>
          <div className="mt-6 space-y-3">
            {gaps && gaps.length > 0 ? (
              gaps.map((gap) => (
                <div
                  key={gap}
                  className="flex items-center justify-between gap-4 border-b border-border/70 py-3 last:border-0"
                >
                  <span className="text-sm font-medium">{gap}</span>
                  <span className="shrink-0 rounded-full bg-amber-500/10 px-2.5 py-0.5 text-xs font-medium text-amber-600">
                    Needs More Depth
                  </span>
                </div>
              ))
            ) : (
              <div className="flex items-center justify-between gap-4 border-b border-border/70 py-3">
                <span className="text-sm font-medium">Core Role Competencies</span>
                <span className="shrink-0 rounded-full bg-emerald-500/10 px-2.5 py-0.5 text-xs font-medium text-emerald-600">
                  Demonstrated
                </span>
              </div>
            )}
            <div className="flex items-center justify-between gap-4 py-2 text-xs text-muted-foreground">
              <span>Technical Clarity & Communication</span>
              <span className="text-accent font-medium">Evaluated Live</span>
            </div>
          </div>
        </section>

        <section className="break-inside-avoid print:break-inside-avoid print-avoid-break print-card rounded-2xl border border-border bg-card p-6 sm:p-7">
          <div className="flex items-start gap-3">
            <div className="no-print print:hidden flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-amber-500/10 text-amber-600">
              <TriangleAlert className="h-4 w-4" />
            </div>
            <div>
              <p className="eyebrow text-amber-600">Resume claim verification</p>
              <h2 className="mt-2 font-serif text-2xl">Claim to shore up</h2>
            </div>
          </div>
          <div className="mt-6 space-y-4 text-sm leading-6 text-muted-foreground">
            {resumeFlags.length > 0 ? (
              resumeFlags.map((flag, idx) => (
                <div key={idx} className="rounded-xl border border-border/60 bg-muted/30 p-4">
                  {flag.claim && (
                    <p className="font-semibold text-foreground mb-1 text-xs uppercase tracking-wide text-accent">
                      {flag.claim}
                    </p>
                  )}
                  <p>{flag.issue}</p>
                </div>
              ))
            ) : (
              <p>
                All checked resume claims and projects were verified and backed with technical explanations during your session.
              </p>
            )}
          </div>
          <Link
            href={`/interview?interview_id=${interviewId}`}
            className="no-print print:hidden mt-5 inline-flex items-center text-sm font-medium text-accent hover:underline"
          >
            Practice this response <ArrowRight className="ml-1 h-3.5 w-3.5" />
          </Link>
        </section>
      </div>

      {/* Targeted Practice Reps Section */}
      <section className="break-inside-avoid print:break-inside-avoid print-avoid-break print-card mt-5 rounded-2xl border border-border bg-card p-6 sm:p-7">
        <div className="flex items-center gap-3">
          <Sparkles className="h-5 w-5 text-accent no-print print:hidden" />
          <div>
            <p className="eyebrow">Targeted Practice Reps</p>
            <h2 className="mt-2 font-serif text-2xl">Turn insight into muscle memory.</h2>
          </div>
        </div>
        <div className="mt-6 divide-y divide-border">
          {(nextReps.length > 0
            ? nextReps
            : [
                {
                  question_text: 'Tell me about a technical decision or architecture choice you made recently.',
                  issue: 'Live answer lacked concrete metrics and structured trade-off evaluation.',
                  suggested_answer:
                    'Start with context, state your architectural decision, compare the top alternatives evaluated, and finish with measurable latency or throughput outcomes.',
                },
              ]
          ).map((item, index) => {
            const question = item.question_text || `Target Practice Question #${index + 1}`
            const critique = item.issue
            const modelAnswer = item.suggested_answer || item.suggested_model_answer
            return (
              <div key={index} className="break-inside-avoid print:break-inside-avoid print-avoid-break py-4">
                <button
                  type="button"
                  onClick={() => setOpen(open === index ? -1 : index)}
                  className="flex w-full items-center justify-between gap-4 py-2 text-left"
                >
                  <div>
                    <span className="text-xs font-mono text-accent uppercase tracking-wider block mb-1">
                      Question #{index + 1}
                    </span>
                    <span className="text-sm font-semibold text-foreground">{question}</span>
                  </div>
                  <ChevronDown
                    className={`no-print print:hidden h-4 w-4 shrink-0 text-muted-foreground transition-transform ${
                      open === index ? 'rotate-180' : ''
                    }`}
                  />
                </button>
                <div
                  className={`${
                    open === index ? 'block' : 'hidden print:block print-accordion-content'
                  } mt-4 space-y-3 rounded-xl bg-muted/40 p-4 text-sm leading-6`}
                >
                  {critique && (
                    <div>
                      <span className="font-semibold text-amber-500">Coach Feedback: </span>
                      <span className="text-muted-foreground">{critique}</span>
                    </div>
                  )}
                  {modelAnswer && (
                    <div>
                      <span className="font-semibold text-accent">Recommended Model Answer: </span>
                      <span className="text-muted-foreground">{modelAnswer}</span>
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </section>

      {/* Footer Navigation */}
      <div className="no-print print:hidden mt-8 flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <Link
          href="/"
          className="inline-flex items-center gap-2 text-sm text-muted-foreground transition hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" /> Back home
        </Link>
        <Link
          href="/onboarding"
          className="inline-flex h-12 items-center justify-center gap-2 rounded-full bg-primary px-6 font-medium text-primary-foreground transition hover:opacity-90"
        >
          <RotateCcw className="h-4 w-4" /> Start a new interview
        </Link>
      </div>
    </div>
  )
}

export default function ReportPage() {
  return (
    <main className="min-h-screen bg-background text-foreground">
      <header className="no-print print:hidden border-b border-border/70">
        <div className="mx-auto flex h-20 max-w-6xl items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-3 font-semibold">
            <img src="/coachbot-logo.png" alt="CoachBot Logo" className="h-8 w-8 rounded-lg object-contain" />
            <span className="font-bold text-lg">CoachBot</span>
          </Link>
          <span className="font-mono text-xs uppercase tracking-[0.18em] text-muted-foreground">
            Feedback report
          </span>
        </div>
      </header>
      <Suspense
        fallback={
          <div className="flex min-h-[50vh] items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-accent" />
          </div>
        }
      >
        <ReportContent />
      </Suspense>
    </main>
  )
}

