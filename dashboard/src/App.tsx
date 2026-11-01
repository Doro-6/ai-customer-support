import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { isLoggedIn, logout } from "@/lib/api"
import { EventsPage } from "@/pages/events"
import { KnowledgeBasePage } from "@/pages/knowledge-base"
import { LoginPage } from "@/pages/login"
import { TicketsPage } from "@/pages/tickets"
export default function App() {
  const [loggedIn, setLoggedIn] = useState(isLoggedIn())
  if (!loggedIn) {
    return <LoginPage onLogin={() => setLoggedIn(true)} />
  }
  function logOut() {
    logout()
    setLoggedIn(false)
  }
  return (
    <div className="mx-auto max-w-6xl p-6">
      <header className="mb-6 flex items-center justify-between">
        <h1 className="text-lg font-semibold">Harbor &amp; Pine support</h1>
        <Button variant="outline" onClick={logOut}>
          Log out
        </Button>
      </header>
      <Tabs defaultValue="events">
        <TabsList>
          <TabsTrigger value="events">Events</TabsTrigger>
          <TabsTrigger value="tickets">Tickets</TabsTrigger>
          <TabsTrigger value="knowledge-base">Knowledge base</TabsTrigger>
        </TabsList>
        <TabsContent value="events" className="mt-4">
          <EventsPage />
        </TabsContent>
        <TabsContent value="tickets" className="mt-4">
          <TicketsPage />
        </TabsContent>
        <TabsContent value="knowledge-base" className="mt-4">
          <KnowledgeBasePage />
        </TabsContent>
      </Tabs>
    </div>
  )
}
