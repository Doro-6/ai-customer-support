export type SupportEvent = {
  id: number
  gmail_message_id: string
  status: string
  attempts: number
  error: string | null
  created_at: string
}
export type Ticket = {
  id: number
  subject: string
  customer_email: string
  category: string | null
  status: string
  proposed_action: string | null
  updated_at: string
}
export type Message = {
  id: number
  direction: "in" | "out"
  from_address: string
  body: string
  created_at: string
}
export type Entry = {
  id: number
  section: string
  title: string
  content: string
}
export function formatDate(value: string) {
  return new Date(value).toLocaleString()
}
