import { BrowserRouter, Route, Routes } from 'react-router-dom';
import './index.css';
import { Sidebar } from './components/Sidebar';
import { ToastContainer } from './components/ToastContainer';
import { useToast } from './hooks/useToast';
import { OverviewPage } from './pages/OverviewPage';
import { EndpointsPage } from './pages/EndpointsPage';
import { EventsPage } from './pages/EventsPage';
import { DlqPage } from './pages/DlqPage';
import { SettingsPage } from './pages/SettingsPage';

function App() {
  const { toasts, show: toast } = useToast();

  return (
    <BrowserRouter>
      <div className="layout">
        <Sidebar />
        <main className="main-content">
          <Routes>
            <Route path="/"          element={<OverviewPage />} />
            <Route path="/endpoints" element={<EndpointsPage toast={toast} />} />
            <Route path="/events"    element={<EventsPage toast={toast} />} />
            <Route path="/dlq"       element={<DlqPage toast={toast} />} />
            <Route path="/settings"  element={<SettingsPage />} />
            <Route path="*"          element={<div className="animate-in"><h1 className="page-title">404</h1><p className="page-sub mt-2">Page not found.</p></div>} />
          </Routes>
        </main>
        <ToastContainer toasts={toasts} />
      </div>
    </BrowserRouter>
  );
}

export default App;
