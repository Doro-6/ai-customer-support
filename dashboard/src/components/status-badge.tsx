import { Badge } from "@/components/ui/badge"
// Black for what needs attention, outlined for the rest.
export function StatusBadge({ status }: { status: string }) {
  const urgent = status === "escalated" || status === "failed"
  return <Badge variant={urgent ? "default" : "outline"}>{status}</Badge>
}
