import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import AccountPage from "@/pages/AccountPage";
import BuildToolPage from "@/pages/BuildToolPage";
import ChatPage from "@/pages/ChatPage";
import RequestsPage from "@/pages/RequestsPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/requests" element={<RequestsPage />} />
        <Route path="/build-tool" element={<BuildToolPage />} />
        <Route path="/account" element={<AccountPage />} />
      </Route>

      <Route path="*" element={<Navigate to="/chat" replace />} />
    </Routes>
  );
}
