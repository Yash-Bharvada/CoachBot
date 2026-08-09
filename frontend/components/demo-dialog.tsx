'use client'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { Clock3, Loader2, X } from 'lucide-react'

type Props = { open: boolean; onOpenChange: (open: boolean) => void }
type Status = 'idle' | 'loading' | 'active' | 'ended' | 'error'

export function DemoDialog({ open, onOpenChange }: Props) {
  const [status, setStatus] = useState<Status>('idle')
  const [seconds, setSeconds] = useState(60)
  const [url, setUrl] = useState('')
  const conversationId = useRef('')
  const started = useRef(false)

  const endConversation = async () => {
    if (!conversationId.current) return
    const id = conversationId.current
    conversationId.current = ''
    try { await fetch('/api/demo-conversation/end', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ conversation_id: id }) }) } catch { /* cleanup is best effort */ }
  }

  useEffect(() => {
    if (!open || started.current) return
    started.current = true
    setStatus('loading')
    try {
      if (sessionStorage.getItem('interview-demo-started') === 'true') { setStatus('error'); return }
      sessionStorage.setItem('interview-demo-started', 'true')
    } catch { /* continue if storage is unavailable */ }
    fetch('/api/demo-conversation', { method: 'POST' })
      .then(async (response) => { const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Unavailable'); return data })
      .then((data) => { conversationId.current = data.conversation_id; setUrl(data.conversation_url); setSeconds(60); setStatus('active') })
      .catch(() => setStatus('error'))
  }, [open])

  useEffect(() => {
    if (!open || status !== 'active') return
    if (seconds <= 0) { void endConversation(); setStatus('ended'); return }
    const timer = window.setTimeout(() => setSeconds((value) => value - 1), 1000)
    return () => window.clearTimeout(timer)
  }, [open, status, seconds])

  const close = async () => { await endConversation(); onOpenChange(false); setStatus('idle'); setUrl(''); started.current = false }
  if (!open) return null
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-primary/55 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="demo-title">
    <div className="relative flex h-[min(760px,calc(100vh-2rem))] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-2xl">
      <div className="flex items-center justify-between border-b border-border px-5 py-4"><div><h2 id="demo-title" className="font-serif text-xl">Live interview demo</h2><p className="text-xs text-muted-foreground">A one-minute taste of the experience</p></div><button type="button" onClick={close} aria-label="Close demo" className="rounded-full p-2 text-muted-foreground hover:bg-muted hover:text-foreground"><X className="h-5 w-5" /></button></div>
      <div className="relative flex min-h-0 flex-1 items-center justify-center bg-[#e8eef1]">
        {status === 'active' && url && <iframe title="Tavus live interview demo" src={url} allow="camera; microphone; autoplay; fullscreen" className="h-full w-full border-0" />}
        {status === 'loading' && <div className="text-center"><Loader2 className="mx-auto h-8 w-8 animate-spin text-accent" /><p className="mt-4 font-medium">Preparing your interviewer…</p><p className="mt-1 text-sm text-muted-foreground">This usually takes a moment.</p></div>}
        {status === 'error' && <div className="max-w-sm px-6 text-center"><p className="font-serif text-2xl">Demo temporarily unavailable</p><p className="mt-3 text-sm leading-6 text-muted-foreground">The live room could not be started. You can still begin a full practice interview.</p><Link href="/onboarding" onClick={close} className="mt-6 inline-flex rounded-full bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground">Start your interview</Link></div>}
        {status === 'ended' && <div className="max-w-sm px-6 text-center"><Clock3 className="mx-auto h-8 w-8 text-accent" /><p className="mt-4 font-serif text-2xl">That’s a taste of it.</p><p className="mt-3 text-sm leading-6 text-muted-foreground">Start your real interview to go deeper and get feedback built around your role.</p><Link href="/onboarding" onClick={close} className="mt-6 inline-flex rounded-full bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground">Start your real interview</Link></div>}
        {status === 'active' && <div className="absolute bottom-4 left-4 flex items-center gap-2 rounded-full bg-primary/90 px-3 py-2 text-xs font-medium text-primary-foreground"><Clock3 className="h-3.5 w-3.5" /> {`00:${String(seconds).padStart(2, '0')}`} remaining</div>}
      </div>
      <div className="flex items-center justify-between border-t border-border px-5 py-3 text-xs text-muted-foreground"><span>Powered by Tavus</span>{status === 'active' && <span>Microphone access may be requested</span>}</div>
    </div>
  </div>
}
