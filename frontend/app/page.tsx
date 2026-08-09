'use client'

import { useState } from 'react'
import Link from 'next/link'
import { ArrowRight, CirclePlay, FileText, Mic2, BarChart3, Sparkles, Target, Video, ShieldCheck } from 'lucide-react'
import { DemoDialog } from '@/components/demo-dialog'

const steps = [
  { number: '01', icon: FileText, title: 'Upload your JD & resume', text: 'The AI reads both and builds context specific to you and the role.' },
  { number: '02', icon: Mic2, title: 'Talk to your interviewer', text: 'A live, face-to-face conversation adapts its difficulty to how you’re doing.' },
  { number: '03', icon: BarChart3, title: 'Get your feedback report', text: 'See confidence, fluency, technical accuracy, and exactly where to improve.' },
]

const features = [
  { icon: Target, title: 'Resume & JD Grounding', text: 'Questions are shaped by your actual background, not generic question banks.' },
  { icon: Sparkles, title: 'Adaptive Difficulty', text: 'The interview gets harder or eases up based on how you answer, live.' },
  { icon: Video, title: 'Real, Expressive Interviewer', text: 'A photorealistic AI interviewer powered by Tavus CVI, not a scripted chatbot.' },
  { icon: BarChart3, title: 'Detailed Feedback Report', text: 'Confidence, tone, fluency, accuracy, and specific weak points with suggested answers.' },
  { icon: ShieldCheck, title: 'Resume Gap Check', text: 'Flags anything you claimed but could not back up live, so you know what to shore up.' },
]

export default function Page() {
  const [demoOpen, setDemoOpen] = useState(false)
  return (
    <main className="min-h-screen overflow-hidden bg-background text-foreground">
      <header className="sticky top-0 z-40 border-b border-border/70 bg-background/90 backdrop-blur-md">
        <div className="mx-auto flex h-20 max-w-7xl items-center justify-between px-6 lg:px-10">
          <Link href="#top" className="flex items-center gap-3 font-semibold tracking-tight" aria-label="CoachBot home">
            <img src="/coachbot-logo.png" alt="CoachBot Logo" className="h-9 w-9 rounded-lg object-contain" />
            <span className="hidden sm:inline font-bold text-lg">CoachBot</span>
          </Link>
          <nav className="hidden items-center gap-8 text-sm text-muted-foreground md:flex">
            <Link href="#how-it-works" className="transition-colors hover:text-foreground">How it works</Link>
            <Link href="#features" className="transition-colors hover:text-foreground">Features</Link>
          </nav>
          <Link href="/onboarding" className="inline-flex items-center gap-2 rounded-full bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground transition-transform hover:-translate-y-0.5">Start Free Interview <ArrowRight className="h-4 w-4" /></Link>
        </div>
      </header>

      <section id="top" className="mx-auto grid max-w-7xl gap-16 px-6 pb-24 pt-20 lg:grid-cols-[1.02fr_.98fr] lg:items-center lg:px-10 lg:pb-32 lg:pt-28">
        <div className="animate-fade-up">
          <p className="mb-6 flex items-center gap-2 text-sm font-medium uppercase tracking-[0.18em] text-accent"><span className="h-px w-8 bg-accent" /> Practice with purpose</p>
          <h1 className="max-w-3xl font-serif text-5xl leading-[1.04] tracking-[-0.045em] text-balance sm:text-6xl lg:text-7xl">The interview is the skill. <em className="text-accent">Practice the real thing.</em></h1>
          <p className="mt-7 max-w-xl text-lg leading-8 text-muted-foreground">Upload your job description and resume, talk with an adaptive AI interviewer face-to-face, and walk away with a feedback report you can act on.</p>
          <div className="mt-9 flex flex-col gap-3 sm:flex-row">
            <Link href="/onboarding" className="inline-flex h-12 items-center justify-center gap-2 rounded-full bg-primary px-6 font-medium text-primary-foreground transition-transform hover:-translate-y-0.5">Start Your Mock Interview <ArrowRight className="h-4 w-4" /></Link>
            <button type="button" onClick={() => setDemoOpen(true)} className="inline-flex h-12 items-center justify-center gap-2 rounded-full border border-border bg-card px-6 font-medium transition-colors hover:bg-muted"><CirclePlay className="h-4 w-4 text-accent" /> Try a 60-Second Live Demo</button>
          </div>
          <div className="mt-10 flex items-center gap-4 text-sm text-muted-foreground"><div className="flex -space-x-2"><span className="avatar bg-[#29435c]">A</span><span className="avatar bg-[#738b78]">M</span><span className="avatar bg-[#b48165]">J</span></div><span>Built for candidates who want to feel ready.</span></div>
        </div>
        <div className="relative">
          <div className="absolute -inset-5 rounded-[2rem] bg-accent/5 blur-2xl" aria-hidden="true" />
          <div className="relative overflow-hidden rounded-[1.75rem] border border-border bg-card shadow-2xl shadow-primary/10">
            <div className="flex items-center justify-between border-b border-border px-5 py-4"><div className="flex items-center gap-2 text-sm font-medium"><span className="h-2 w-2 rounded-full bg-accent" />Live interview room</div><span className="rounded-full bg-muted px-3 py-1 text-xs text-muted-foreground">Preview</span></div>
            <div className="flex aspect-[4/3] flex-col items-center justify-center bg-[#e8eef1] px-8 text-center"><div className="mb-5 flex h-20 w-20 items-center justify-center rounded-full border-8 border-card bg-primary text-2xl font-serif text-primary-foreground shadow-xl">A</div><p className="font-serif text-2xl text-primary">Meet your interviewer</p><p className="mt-2 max-w-xs text-sm leading-6 text-muted-foreground">A thoughtful, adaptive conversation built around your next role.</p><button type="button" onClick={() => setDemoOpen(true)} className="mt-6 inline-flex items-center gap-2 rounded-full bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground"><CirclePlay className="h-4 w-4" /> Start preview</button></div>
            <div className="flex items-center justify-between px-5 py-4 text-xs text-muted-foreground"><span>Face-to-face practice</span><span>01:00 demo</span></div>
          </div>
        </div>
      </section>

      <section id="how-it-works" className="border-y border-border bg-muted/45 px-6 py-24 lg:px-10"><div className="mx-auto max-w-7xl"><div className="mb-14 max-w-2xl"><p className="eyebrow">A better rehearsal</p><h2 className="section-title">From preparation to progress.</h2></div><div className="grid gap-5 lg:grid-cols-3">{steps.map((step) => <article key={step.number} className="rounded-2xl border border-border bg-card p-7 transition-transform hover:-translate-y-1"><div className="mb-12 flex items-center justify-between"><span className="font-mono text-sm text-accent">{step.number}</span><step.icon className="h-5 w-5 text-accent" /></div><h3 className="font-serif text-2xl">{step.title}</h3><p className="mt-3 leading-7 text-muted-foreground">{step.text}</p></article>)}</div></div></section>

      <section id="features" className="mx-auto max-w-7xl px-6 py-24 lg:px-10"><div className="grid gap-14 lg:grid-cols-[.75fr_1.25fr]"><div><p className="eyebrow">More than questions</p><h2 className="section-title">Practice that knows your context.</h2><p className="mt-5 max-w-sm leading-7 text-muted-foreground">Generic question lists cannot tell you what you missed. This can.</p></div><div className="grid gap-4 sm:grid-cols-2">{features.map((feature, i) => <article key={feature.title} className={`rounded-2xl border border-border p-6 ${i === 2 ? 'bg-primary text-primary-foreground sm:col-span-2' : 'bg-card'}`}><feature.icon className={`h-5 w-5 ${i === 2 ? 'text-accent-foreground' : 'text-accent'}`} /><h3 className="mt-8 font-serif text-xl">{feature.title}</h3><p className={`mt-2 text-sm leading-6 ${i === 2 ? 'text-primary-foreground/70' : 'text-muted-foreground'}`}>{feature.text}</p></article>)}</div></div></section>

      <section className="border-y border-border bg-muted/30 px-6 py-5"><div className="mx-auto flex max-w-7xl flex-col items-center justify-center gap-3 text-center text-xs uppercase tracking-[0.16em] text-muted-foreground sm:flex-row sm:gap-6"><span>Powered by</span><span className="font-semibold tracking-normal text-foreground">Tavus CVI</span><span className="hidden h-1 w-1 rounded-full bg-accent sm:block" /><span className="font-semibold tracking-normal text-foreground">Groq</span></div></section>

      <section className="px-6 py-24 lg:px-10"><div className="mx-auto flex max-w-5xl flex-col items-center rounded-[2rem] bg-primary px-6 py-16 text-center text-primary-foreground sm:px-12"><p className="eyebrow text-accent-foreground">Your next interview starts here</p><h2 className="mt-5 max-w-2xl font-serif text-4xl leading-tight text-balance sm:text-5xl">You do not need more advice. You need a room to practice in.</h2><Link href="/onboarding" className="mt-8 inline-flex items-center gap-2 rounded-full bg-card px-6 py-3 font-medium text-foreground transition-transform hover:-translate-y-0.5">Start Your Mock Interview <ArrowRight className="h-4 w-4" /></Link></div></section>
      <footer className="border-t border-border px-6 py-8 lg:px-10"><div className="mx-auto flex max-w-7xl flex-col justify-between gap-4 text-sm text-muted-foreground sm:flex-row sm:items-center"><span className="font-medium text-foreground font-bold">CoachBot</span><div className="flex gap-6"><Link href="#how-it-works" className="hover:text-foreground">How it works</Link><Link href="#features" className="hover:text-foreground">Features</Link></div></div></footer>
      <DemoDialog open={demoOpen} onOpenChange={setDemoOpen} />
    </main>
  )
}

