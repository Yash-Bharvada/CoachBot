import { NextResponse } from 'next/server'

export async function POST(request: Request) {
  const { conversation_id: conversationId } = await request.json().catch(() => ({}))
  if (typeof conversationId !== 'string' || !conversationId || conversationId.length > 200) return NextResponse.json({ error: 'Invalid conversation identifier' }, { status: 400 })
  const apiKey = process.env.TAVUS_API_KEY
  if (!apiKey) return NextResponse.json({ success: true })
  try {
    const response = await fetch(`https://tavusapi.com/v2/conversations/${encodeURIComponent(conversationId)}/end`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'x-api-key': apiKey } })
    if (!response.ok) return NextResponse.json({ error: 'Unable to end demo' }, { status: 502 })
    return NextResponse.json({ success: true })
  } catch { return NextResponse.json({ error: 'Unable to end demo' }, { status: 502 }) }
}
