import { CheckCircle2, XCircle } from 'lucide-react';
import type { Toast } from '../types';

export const ToastContainer = ({ toasts }: { toasts: Toast[] }) => (
  <div className="toast-container">
    {toasts.map(t => (
      <div key={t.id} className={`toast toast-${t.type}`}>
        {t.type === 'success' ? <CheckCircle2 size={15} /> : <XCircle size={15} />}
        {t.message}
      </div>
    ))}
  </div>
);
