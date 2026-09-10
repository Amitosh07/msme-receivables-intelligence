import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../features/auth";
import { LoadingState } from "./States";
export function ProtectedRoute() { const { user, loading } = useAuth(); if (loading) return <main className="auth-loading"><LoadingState label="Checking your session…" /></main>; return user ? <Outlet /> : <Navigate to="/login" replace />; }
