import { NextResponse } from 'next/server'

// Required server-only environment variables: TAVUS_API_KEY, TAVUS_DEMO_PAL_ID, TAVUS_DEMO_FACE_ID.
export async function POST() {
  const apiKey = process.env.TAVUS_API_KEY
  const palId = process.env.TAVUS_DEMO_PAL_ID
  const faceId = process.env.TAVUS_DEMO_FACE_ID
  if (!apiKey || !palId || !faceId) return NextResponse.json({ error: 'Demo temporarily unavailable' }, { status: 503 })
  try {
    const response = await fetch('https://tavusapi.com/v2/conversations', { method: 'POST', headers: { 'Content-Type': 'application/json', 'x-api-key': apiKey }, body: JSON.stringify({ pal_id: palId, face_id: faceId, conversation_name: 'Interview Prep Simulator demo', conversational_context: 'Hi, I am your AI interviewer. Ask me anything about how this works, or just say hi. Keep this welcoming demonstration concise and helpful.', properties: { max_call_duration: 60 } }) })
    const data = await response.json()
    if (!response.ok || !data.conversation_url || !data.conversation_id) return NextResponse.json({ error: 'Demo temporarily unavailable' }, { status: 502 })
    return NextResponse.json({ conversation_url: data.conversation_url, conversation_id: data.conversation_id })
  } catch { return NextResponse.json({ error: 'Demo temporarily unavailable' }, { status: 502 }) }
}
