import { useEffect, useState } from "react"
import { StatusBadge } from "@/components/status-badge"
import { Button } from "@/components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { api } from "@/lib/api"
import { formatDate, type SupportEvent } from "@/lib/types"
export function EventsPage() {
  const [events, setEvents] = useState<SupportEvent[]>([])
  const [checking, setChecking] = useState(false)
  const load = () => api<SupportEvent[]>("/events").then(setEvents)
  useEffect(() => {
    load()
  }, [])
  async function checkInbox() {
    setChecking(true)
    try {
      await api("/poll", { method: "POST" })
      await load()
    } finally {
      setChecking(false)
    }
  }
  return (
    <div className="flex flex-col gap-4">
      <div>
        <Button onClick={checkInbox} disabled={checking}>
          {checking ? "Checking..." : "Check inbox now"}
        </Button>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>ID</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Attempts</TableHead>
            <TableHead>Error</TableHead>
            <TableHead>Created</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {events.map((event) => (
            <TableRow key={event.id}>
              <TableCell>{event.id}</TableCell>
              <TableCell>
                <StatusBadge status={event.status} />
              </TableCell>
              <TableCell>{event.attempts}</TableCell>
              <TableCell className="max-w-xs truncate">{event.error}</TableCell>
              <TableCell>{formatDate(event.created_at)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
