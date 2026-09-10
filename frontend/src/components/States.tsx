import { AlertCircle, Inbox, LoaderCircle } from "./Icons";
export function LoadingState({ label = "Loading…" }: { label?: string }) { return <div className="state"><LoaderCircle className="spin" size={22} /><span>{label}</span></div>; }
export function ErrorState({ message, retry }: { message: string; retry?: () => void }) { return <div className="state error" role="alert"><AlertCircle size={22} /><div><strong>We couldn’t load this view.</strong><span>{message}</span>{retry && <button className="text-button" onClick={retry}>Try again</button>}</div></div>; }
export function EmptyState({ title, detail, action }: { title: string; detail: string; action?: React.ReactNode }) { return <div className="empty"><Inbox size={26} /><h2>{title}</h2><p>{detail}</p>{action}</div>; }
