import ProtectedShell from "@/components/ProtectedShell";
import ChatWorkspace from "@/components/chat/ChatWorkspace";

export default function ChatLandingPage() {
  return (
    <ProtectedShell title="Chat Console">
      <ChatWorkspace />
    </ProtectedShell>
  );
}
