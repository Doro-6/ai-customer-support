import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api"
import type { Entry } from "@/lib/types"
export function KnowledgeBasePage() {
  const [entries, setEntries] = useState<Entry[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [title, setTitle] = useState("")
  const [content, setContent] = useState("")
  const [saved, setSaved] = useState(false)
  useEffect(() => {
    api<Entry[]>("/knowledge-base").then(setEntries)
  }, [])
  const sections = [...new Set(entries.map((entry) => entry.section))]
  function select(entry: Entry) {
    setSelectedId(entry.id)
    setTitle(entry.title)
    setContent(entry.content)
    setSaved(false)
  }
  async function save() {
    const updated = await api<Entry>(`/knowledge-base/${selectedId}`, {
      method: "PUT",
      body: JSON.stringify({ title, content }),
    })
    setEntries(entries.map((entry) => (entry.id === updated.id ? updated : entry)))
    setSaved(true)
  }
  return (
    <div className="grid gap-6 lg:grid-cols-[260px_1fr]">
      <nav className="flex flex-col gap-4">
        {sections.map((section) => (
          <div key={section}>
            <p className="mb-1 text-xs font-medium text-muted-foreground uppercase">{section}</p>
            {entries
              .filter((entry) => entry.section === section)
              .map((entry) => (
                <button
                  key={entry.id}
                  onClick={() => select(entry)}
                  className={`block w-full rounded px-2 py-1 text-left text-sm hover:bg-muted ${
                    entry.id === selectedId ? "bg-muted font-medium" : ""
                  }`}
                >
                  {entry.title}
                </button>
              ))}
          </div>
        ))}
      </nav>
      {selectedId === null ? (
        <p className="text-sm text-muted-foreground">Select an entry to edit it.</p>
      ) : (
        <div className="flex flex-col gap-3">
          <Input value={title} onChange={(e) => setTitle(e.target.value)} />
          <Textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            className="min-h-96 font-mono text-sm"
          />
          <div className="flex items-center gap-3">
            <Button onClick={save}>Save</Button>
            {saved && <span className="text-sm text-muted-foreground">Saved</span>}
          </div>
        </div>
      )}
    </div>
  )
}
