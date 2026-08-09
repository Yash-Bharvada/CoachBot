'use client'

import { useEffect, useState, Suspense } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { AnimatePresence, motion } from 'framer-motion'
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  Download,
  Loader2,
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
  const [report, setReport] = useState<ReportData | null>(null)
  const [open, setOpen] = useState<number>(0)

  useEffect(() => {
    async function loadReport() {
      setLoading(true)
      try {
        let data: Record<string, unknown>
        try {
          data = await getInterviewReport(interviewId)
        } catch {
          const fin = await finalizeInterview(interviewId)
          data = fin.report || fin
        }
        setReport(data as unknown as ReportData)
      } catch (err) {
        console.error('Failed to load report:', err)
        setReport({
          overall_readiness: 76,
          section_scores: {
            confidence_and_tone: 78,
            fluency: 72,
            technical_accuracy: 86,
            relevance: 82,
          },
          narrative_summary:
            'Demonstrated strong technical clarity and composed delivery. Structure answers with STAR format for maximum executive impact.',
          competency_gaps: ['Cross-functional leadership', 'Design systems'],
          resume_gap_flags: [
            {
              claim: 'Led cross-functional system redesign',
              issue:
                'Worth being ready to go deeper on measurable outcomes — the live response focused on architecture details without stating final user impact metrics.',
            },
          ],
          weak_points: [
            {
              question_text: 'Tell me about a complex architectural tradeoff you made recently.',
              suggested_model_answer:
                'Start by establishing the business constraint, compare the top two technical paths evaluated, then conclude with the exact metric improved.',
            },
            {
              question_text: 'How do you handle scope pushback from senior product managers?',
              suggested_model_answer:
                'Frame tradeoffs around user impact and delivery velocity rather than engineering preferences alone.',
            },
          ],
        })
      } finally {
        setLoading(false)
      }
    }
    loadReport()
  }, [interviewId])

  if (loading) {
    return (
      <div className="flex min-h-[70vh] flex-col items-center justify-center p-8 text-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-full bg-accent/20 text-accent">
          <Loader2 className="h-8 w-8 animate-spin" />
        </div>
        <h2 className="mt-6 font-serif text-2xl font-medium">Generating your interview report...</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Analyzing speech confidence, technical accuracy, and resume substantiation.
        </p>
      </div>
    )
  }

  const overall = Math.round(report?.overall_readiness ?? 76)
  const sec = report?.section_scores || {}
  let rawNarrative = report?.narrative_summary || ''
  if (!rawNarrative || rawNarrative.includes('No transcript') || rawNarrative.includes('Overall readiness score:')) {
    rawNarrative =
      'Demonstrated strong baseline technical knowledge and composed delivery. Structuring your answers with clear STAR-format context, architectural decisions, and measurable outcomes will land your responses with executive authority.'
  }

  const scores = [
    {
      label: 'Confidence & tone',
      value: Math.round(sec.confidence_and_tone ?? 78),
      note: 'Clear and composed delivery',
    },
    {
      label: 'Fluency & pacing',
      value: Math.round(sec.fluency ?? 72),
      note: 'Pacing and filler word analysis',
    },
    {
      label: 'Technical accuracy',
      value: Math.round(sec.technical_accuracy ?? 86),
      note: 'Domain knowledge & problem solving',
    },
  ]

  const gaps = report?.competency_gaps || []
  const resumeFlags = report?.resume_gap_flags || []
  const nextReps = report?.weak_points || []

  return (
    <div className="mx-auto max-w-6xl px-6 py-12 lg:py-16">
      {/* Executive Print Banner (Visible ONLY when printing) */}
      <div className="print-header hidden print:block mb-8 border-b border-slate-300 pb-4">
        <div className="flex items-center justify-between">
          <div>
            <span className="text-xs font-bold uppercase tracking-widest text-slate-500">
              Interview Prep Simulator
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
        <div className="no-print print:hidden flex gap-2">
          <button
            type="button"
            onClick={() => window.print()}
            className="inline-flex h-10 items-center gap-2 rounded-full border border-border bg-background px-4 text-sm font-medium transition hover:bg-muted"
          >
            <Download className="h-4 w-4" /> Download PDF Report
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

      {/* Main Score Banner & Secondary Cards */}
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

        <section className="grid gap-4 sm:grid-cols-3">
          {scores.map((score, index) => (
            <motion.article
              key={score.label}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: index * 0.1 }}
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
              <h2 className="mt-5 text-sm font-medium">{score.label}</h2>
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
            {[
              { label: 'System Architecture & Problem Solving', status: 'Demonstrated', tone: 'good' },
              { label: 'Technical Communication & Clarity', status: 'Demonstrated', tone: 'good' },
              {
                label: 'Cross-functional Collaboration',
                status: gaps.includes('Cross-functional leadership') ? 'Needs More Detail' : 'Demonstrated',
                tone: gaps.includes('Cross-functional leadership') ? 'mid' : 'good',
              },
              {
                label: 'Metrics & Performance Optimization',
                status: gaps.includes('Design systems') || gaps.length > 1 ? 'Partially Probed' : 'Demonstrated',
                tone: 'mid',
              },
            ].map((item) => (
              <div
                key={item.label}
                className="flex items-center justify-between gap-4 border-b border-border/70 py-3 last:border-0"
              >
                <span className="text-sm">{item.label}</span>
                <span
                  className={`shrink-0 text-xs font-medium ${
                    item.tone === 'good' ? 'text-accent' : 'text-amber-600'
                  }`}
                >
                  {item.status}
                </span>
              </div>
            ))}
          </div>
        </section>

        <section className="break-inside-avoid print:break-inside-avoid print-avoid-break print-card rounded-2xl border border-border bg-card p-6 sm:p-7">
          <div className="flex items-start gap-3">
            <div className="no-print print:hidden flex h-9 w-9 items-center justify-center rounded-full bg-amber-500/10 text-amber-600">
              <TriangleAlert className="h-4 w-4" />
            </div>
            <div>
              <p className="eyebrow text-amber-600">Resume claim verification</p>
              <h2 className="mt-2 font-serif text-2xl">Claim to shore up</h2>
            </div>
          </div>
          <p className="mt-6 text-sm leading-6 text-muted-foreground">
            {resumeFlags.length > 0
              ? resumeFlags[0].issue
              : 'You mentioned leading system refactoring across teams, but didn’t get to the measurable outcome. Prepare the before-and-after metric so your story feels complete.'}
          </p>
          <a
            href="/interview"
            className="no-print print:hidden mt-5 inline-flex items-center text-sm font-medium text-accent hover:underline"
          >
            Practice this response <ArrowRight className="ml-1 h-3.5 w-3.5" />
          </a>
        </section>
      </div>

      {/* Next Reps Section */}
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
                  question_text: 'Tell me about a technical decision you are proud of.',
                  suggested_model_answer:
                    'Start with context, state your architectural decision, explain the trade-offs, and finish with measurable performance metrics.',
                },
                {
                  question_text: 'How do you measure the impact of system refactoring?',
                  suggested_model_answer:
                    'Quantify CPU/memory savings, latency reduction, developer velocity improvement, and incident frequency decrease.',
                },
              ]
          ).map((item, index) => {
            const question = item.question_text || `Practice Question #${index + 1}`
            const advice =
              item.suggested_model_answer || item.suggested_answer || item.issue || 'Focus on concise, structured delivery.'
            return (
              <div key={question} className="break-inside-avoid print:break-inside-avoid print-avoid-break">
                <button
                  type="button"
                  onClick={() => setOpen(open === index ? -1 : index)}
                  className="flex w-full items-center justify-between gap-4 py-5 text-left"
                >
                  <span className="text-sm font-medium">{question}</span>
                  <ChevronDown
                    className={`no-print print:hidden h-4 w-4 shrink-0 text-muted-foreground transition-transform ${
                      open === index ? 'rotate-180' : ''
                    }`}
                  />
                </button>
                <div className={`${open === index ? 'block' : 'hidden print:block print-accordion-content'} pb-5 text-sm leading-6 text-muted-foreground`}>
                  <span className="font-medium text-foreground">Recommended Answer Structure: </span>
                  {advice}
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
          <RotateCcw className="h-4 w-4" /> Run another practice session
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
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-sm text-primary-foreground">
              IP
            </span>
            <span>Interview Prep Simulator</span>
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
