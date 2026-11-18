import { AnimatePresence, motion } from 'framer-motion';
import { AlertTriangle, X } from 'lucide-react';
import { useEffect, useId, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import clsx from 'clsx';
import { Button } from './primitives';

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  size?: 'sm' | 'md' | 'lg' | 'xl';
}

const WIDTH = { sm: 'max-w-md', md: 'max-w-lg', lg: 'max-w-2xl', xl: 'max-w-4xl' };

/** Accessible dialog: focus trap, Escape to close, scroll lock, returns focus on close. */
export function Modal({ open, onClose, title, description, children, footer, size = 'md' }: ModalProps) {
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descId = useId();

  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const t = window.setTimeout(() => {
      const first = panel.current?.querySelector<HTMLElement>('input,select,textarea,button:not([data-close])');
      (first ?? panel.current)?.focus();
    }, 30);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); onClose(); }
      if (e.key === 'Tab' && panel.current) {
        const f = panel.current.querySelectorAll<HTMLElement>(
          'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])');
        if (!f.length) return;
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      window.clearTimeout(t);
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = overflow;
      prev?.focus?.();
    };
  }, [open, onClose]);

  return createPortal(
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center sm:p-4">
          <motion.div className="absolute inset-0 bg-slate-950/45 backdrop-blur-[2px]" onClick={onClose}
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }} />
          <motion.div ref={panel} role="dialog" aria-modal="true" aria-labelledby={titleId}
            aria-describedby={description ? descId : undefined} tabIndex={-1}
            className={clsx('relative w-full bg-surface border border-line shadow-pop flex flex-col max-h-[92vh]',
              'rounded-t-2xl sm:rounded-2xl', WIDTH[size])}
            initial={{ opacity: 0, y: 24, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.98 }} transition={{ type: 'spring', stiffness: 420, damping: 34 }}>
            <div className="flex items-start justify-between gap-4 px-5 pt-5 pb-3">
              <div className="min-w-0">
                <h2 id={titleId} className="text-base font-semibold text-ink">{title}</h2>
                {description && <p id={descId} className="mt-1 text-[13px] text-muted">{description}</p>}
              </div>
              <button data-close onClick={onClose} aria-label="Close dialog"
                className="rounded-md p-1.5 text-muted hover:text-ink hover:bg-surface-2"><X className="h-4 w-4" /></button>
            </div>
            <div className="px-5 pb-5 overflow-y-auto">{children}</div>
            {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-line px-5 py-3.5 bg-surface-2/60 rounded-b-2xl">{footer}</div>}
          </motion.div>
        </div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

export function ConfirmDialog({ open, onClose, onConfirm, title, body, confirmLabel = 'Confirm', destructive,
  loading }: { open: boolean; onClose: () => void; onConfirm: () => void; title: string; body: ReactNode;
  confirmLabel?: string; destructive?: boolean; loading?: boolean }) {
  return (
    <Modal open={open} onClose={onClose} size="sm" title={
      <span className="flex items-center gap-2">
        {destructive && <AlertTriangle className="h-4 w-4 text-red-600" aria-hidden />}{title}
      </span>}
      footer={<>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button variant={destructive ? 'danger' : 'primary'} loading={loading} onClick={onConfirm}>{confirmLabel}</Button>
      </>}>
      <div className="text-sm text-muted leading-relaxed">{body}</div>
    </Modal>
  );
}
