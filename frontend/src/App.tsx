import { Navigate, Route, Routes } from "react-router-dom";
import { getToken } from "./api";
import { FeedbackProvider } from "./feedback";
import { Layout } from "./layout";
import { Dashboard } from "./pages/Dashboard";
import { History } from "./pages/History";
import { Inventory } from "./pages/Inventory";
import { ChangePassword } from "./pages/ChangePassword";
import { Login } from "./pages/Login";
import { Playbooks } from "./pages/Playbooks";
import { Settings } from "./pages/Settings";

function Private({ children }: { children: React.ReactNode }) {
  if (!getToken()) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/change-password"
        element={
          <Private>
            <ChangePassword />
          </Private>
        }
      />
      <Route
        path="/"
        element={
          <Private>
            <FeedbackProvider>
              <Layout />
            </FeedbackProvider>
          </Private>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="playbooks" element={<Playbooks />} />
        <Route path="inventory" element={<Inventory />} />
        <Route path="history" element={<History />} />
        <Route path="settings" element={<Settings />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
