'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { AnimatePresence, motion } from 'framer-motion'
import { ArrowRight, Check, FileText, Loader2, Upload } from 'lucide-react'
import { createInterviewOnboarding } from '@/lib/api-client'

export default function OnboardingPage() {
  const router = useRouter()
  const [role, setRole] = useState('Senior Product Designer')
  const [company, setCompany] = useState('')
  const [jd, setJd] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [submitted, setSubmitted] = useState(false)

  function handleFile(next: File | undefined) {
    if (!next) return
    if (
      !['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'].includes(
        next.type,
      ) &&
      !next.name.endsWith('.pdf') &&
      !next.name.endsWith('.docx')
    ) {
      setError('Please upload a PDF or DOCX file.')
      return
    }
    setError('')
    setFile(next)
  }

  async function submit() {
    if (!role.trim() || !jd.trim()) {
      setError('Please enter a target role and paste the job description.')
      return
    }
    setLoading(true)
    setError('')
    try {
      const formData = new FormData()
      formData.append('job_title', role.trim())
      if (company.trim()) {
        formData.append('company_name', company.trim())
      }
      formData.append('job_description', jd.trim())
      if (file) {
        formData.append('resume', file)
      }

      const res = await createInterviewOnboarding(formData)
      if (res && res.interview_id) {
        setSubmitted(true)
        router.push(`/interview?interview_id=${res.interview_id}`)
      } else {
        throw new Error('Failed to create interview session.')
      }
    } catch (err: unknown) {
      console.error('Onboarding submit error:', err)
      const msg = err instanceof Error ? err.message : 'Unable to parse job description or resume.'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="min-h-screen bg-background text-foreground">
      <header className="border-b border-border/70 bg-background/90">
        <div className="mx-auto flex h-20 max-w-6xl items-center justify-between px-6">
          <a href="/" className="flex items-center gap-3 font-semibold tracking-tight">
            <img src="/coachbot-logo.png" alt="CoachBot Logo" className="h-9 w-9 rounded-lg object-contain" />
            <span className="font-bold text-lg">CoachBot</span>
          </a>
          <span className="font-mono text-xs uppercase tracking-[0.18em] text-muted-foreground">Step 1 / 3</span>
        </div>
      </header>
      <div className="mx-auto grid max-w-6xl gap-10 px-6 py-12 lg:grid-cols-[.75fr_1.25fr] lg:py-20">
        <div className="pt-4">
          <p className="eyebrow">Build your room</p>
          <h1 className="section-title max-w-md">Give your interviewer something real to work with.</h1>
          <p className="mt-5 max-w-md leading-7 text-muted-foreground">
            We use your role, job description, and resume to shape a focused rehearsal. Nothing generic, nothing wasted.
          </p>
          <div className="mt-10 space-y-4 text-sm text-muted-foreground">
            <div className="flex gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent/15 text-accent">
                <Check className="h-3.5 w-3.5" />
              </span>
              Questions grounded in your experience
            </div>
            <div className="flex gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent/15 text-accent">
                <Check className="h-3.5 w-3.5" />
              </span>
              A calm, face-to-face practice room
            </div>
            <div className="flex gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent/15 text-accent">
                <Check className="h-3.5 w-3.5" />
              </span>
              Feedback you can act on immediately
            </div>
          </div>
        </div>
        <motion.form
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
          className="rounded-[1.5rem] border border-border bg-card p-6 shadow-xl shadow-primary/5 sm:p-8"
          layout
        >
          <div className="grid gap-6 sm:grid-cols-2">
            <label className="grid gap-2 text-sm font-medium">
              <span>Target role</span>
              <input
                value={role}
                onChange={(e) => setRole(e.target.value)}
                placeholder="e.g. Senior Backend Engineer"
                className="h-12 rounded-xl border border-input bg-background px-4 font-normal outline-none focus:ring-2 focus:ring-ring"
              />
            </label>
            <label className="grid gap-2 text-sm font-medium">
              <span>
                Company <span className="font-normal text-muted-foreground">(optional)</span>
              </span>
              <input
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                placeholder="Your next company"
                className="h-12 rounded-xl border border-input bg-background px-4 font-normal outline-none focus:ring-2 focus:ring-ring"
              />
            </label>
          </div>
          <label className="mt-6 grid gap-2 text-sm font-medium">
            Job description
            <textarea
              value={jd}
              onChange={(e) => setJd(e.target.value)}
              placeholder="Paste the job description here..."
              rows={7}
              className="resize-y rounded-xl border border-input bg-background px-4 py-3 font-normal leading-6 outline-none focus:ring-2 focus:ring-ring"
            />
          </label>
          <div className="mt-6 grid gap-2 text-sm font-medium">
            <span>Resume</span>
            <label className="group flex min-h-36 cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed border-accent/50 bg-accent/[0.04] px-6 text-center transition-colors hover:bg-accent/[0.08]">
              <input
                type="file"
                accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                className="sr-only"
                onChange={(e) => handleFile(e.target.files?.[0])}
              />
              {file ? (
                <>
                  <FileText className="h-7 w-7 text-accent" />
                  <span className="mt-2 text-sm">{file.name}</span>
                  <span className="mt-1 text-xs text-muted-foreground">Ready to parse</span>
                </>
              ) : (
                <>
                  <Upload className="h-7 w-7 text-accent" />
                  <span className="mt-2">Drop your resume here or browse</span>
                  <span className="mt-1 text-xs font-normal text-muted-foreground">PDF or DOCX · max 10 MB</span>
                </>
              )}
            </label>
          </div>
          {error && (
            <p role="alert" className="mt-4 rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </p>
          )}
          <AnimatePresence>
            {submitted && (
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                className="mt-5 flex items-center gap-2 text-sm text-accent"
              >
                <Check className="h-4 w-4" /> Room created! Redirecting to live interviewer...
              </motion.div>
            )}
          </AnimatePresence>
          <button
            type="submit"
            disabled={loading || submitted}
            className="mt-6 inline-flex h-12 w-full items-center justify-center gap-2 rounded-full bg-primary px-6 font-medium text-primary-foreground transition-transform hover:-translate-y-0.5 disabled:cursor-wait disabled:opacity-70"
          >
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" /> Parsing resume & job description...
              </>
            ) : (
              <>
                Continue to practice room <ArrowRight className="h-4 w-4" />
              </>
            )}
          </button>
        </motion.form>
      </div>
    </main>
  )
}
