import { useEffect, useState } from "react"
import { StatusBadge } from "@/components/status-badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { api } from "@/lib/api"
import { formatDate, type Message, type Ticket } from "@/lib/types"
export function TicketsPage() {
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [selected, setSelected] = useState<Ticket | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  useEffect(() => {
    api<Ticket[]>("/tickets").then(setTickets)
  }, [])
  async function open(ticket: Ticket) {
    setSelected(ticket)
    setMessages(await api<Message[]>(`/tickets/${ticket.id}/messages`))
  }
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Subject</TableHead>
            <TableHead>Customer</TableHead>
            <TableHead>Category</TableHead>
            <TableHead>Status</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tickets.map((ticket) => (
            <TableRow
              key={ticket.id}
              onClick={() => open(ticket)}
              data-state={ticket.id === selected?.id ? "selected" : undefined}
              className="cursor-pointer"
            >
              <TableCell className="max-w-48 truncate">{ticket.subject}</TableCell>
              <TableCell>{ticket.customer_email}</TableCell>
              <TableCell>{ticket.category}</TableCell>
              <TableCell>
                <StatusBadge status={ticket.status} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {selected ? (
        <div className="flex flex-col gap-3">
          <h2 className="font-medium">{selected.subject}</h2>
          {selected.proposed_action && (
            <p className="text-sm">Proposed action: {selected.proposed_action}</p>
          )}
          {messages.map((message) => (
            <div
              key={message.id}
              className={
                message.direction === "out"
                  ? "ml-10 rounded-lg bg-primary p-3 text-primary-foreground"
                  : "mr-10 rounded-lg border p-3"
              }
            >
              <p className="mb-1 text-xs opacity-70">
                {message.from_address}, {formatDate(message.created_at)}
              </p>
              <p className="text-sm whitespace-pre-wrap">{message.body}</p>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">Select a ticket to read the conversation.</p>
      )}
    </div>
  )
}
