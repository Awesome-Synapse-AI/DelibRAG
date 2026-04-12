import ProtectedShell from "@/components/ProtectedShell";
import ChatWorkspace from "@/components/chat/ChatWorkspace";

export default function SessionChatPage({ params }: { params: { sessionId: string } }) {
  return (
    <ProtectedShell title="Chat Console">
      <ChatWorkspace initialSessionId={params.sessionId} />
    </ProtectedShell>
  );
}
