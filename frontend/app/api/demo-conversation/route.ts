import { NextResponse } from 'next/server'

export async function POST() {
  const primaryApiKey = process.env.TAVUS_API_KEY || '9ddb13b817084782a5f22609bd7da168'
  const primaryPalId = process.env.TAVUS_PAL_ID || process.env.TAVUS_DEMO_PAL_ID || 'p22c050aaaa9'
  const backupApiKey = process.env.TAVUS_BACKUP_API_KEY || '8b60592cb8eb473fbc4786251916bb71'
  const backupPalId = process.env.TAVUS_BACKUP_PAL_ID || 'p11d7be7266d'

  const candidates = [
    { apiKey: primaryApiKey, palId: primaryPalId },
    { apiKey: backupApiKey, palId: backupPalId },
  ]

  for (const cand of candidates) {
    if (!cand.apiKey || !cand.palId) continue
    try {
      const response = await fetch('https://tavusapi.com/v2/conversations', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': cand.apiKey,
        },
        body: JSON.stringify({
          pal_id: cand.palId,
          conversation_name: 'CoachBot Technical Interview Demo',
          conversational_context:
            'Hi, I am your AI technical interviewer. Welcome to CoachBot! Ask me anything about how this works, or just say hi.',
          properties: { max_call_duration: 180 },
        }),
      })
      const data = await response.json()
      if (response.ok && data.conversation_url && data.conversation_id) {
        return NextResponse.json({
          conversation_url: data.conversation_url,
          conversation_id: data.conversation_id,
        })
      }
    } catch {
      // Continue to next candidate
    }
  }

  return NextResponse.json({ error: 'Demo temporarily unavailable' }, { status: 502 })
}

