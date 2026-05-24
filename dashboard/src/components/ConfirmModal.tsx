import { type ReactNode } from 'react';

export interface ConfirmModalConfig {
  title: string;
  message: ReactNode;
  confirmText?: string;
  cancelText?: string;
  isDanger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

interface ConfirmModalProps {
  config: ConfirmModalConfig | null;
}

export const ConfirmModal = ({ config }: ConfirmModalProps) => {
  if (!config) return null;

  return (
    <div className="modal-overlay" onClick={config.onCancel}>
      <div className="modal-content" onClick={e => e.stopPropagation()}>
        <div className="modal-header">{config.title}</div>
        <div className="modal-body">{config.message}</div>
        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={config.onCancel}>
            {config.cancelText || 'Cancel'}
          </button>
          <button 
            className={`btn ${config.isDanger ? 'btn-danger' : 'btn-primary'}`} 
            onClick={config.onConfirm}
          >
            {config.confirmText || 'Confirm'}
          </button>
        </div>
      </div>
    </div>
  );
};
