import { createBrowserRouter, type RouteObject } from 'react-router-dom';
import { AppShell } from '@/layout/AppShell';
import ExecutiveOverview from '@/pages/ExecutiveOverview';
import SupplierIntelligence from '@/pages/SupplierIntelligence';
import SupplierDetail from '@/pages/SupplierDetail';
import RiskCommandCenter from '@/pages/RiskCommandCenter';
import RiskCaseDetail from '@/pages/RiskCaseDetail';
import AiRecommendations from '@/pages/AiRecommendations';
import ApprovalCenter from '@/pages/ApprovalCenter';
import WhatIfSimulator from '@/pages/WhatIfSimulator';
import IntegrationCenter from '@/pages/IntegrationCenter';
import AuditCenter from '@/pages/AuditCenter';
import TenantSettings from '@/pages/TenantSettings';
import NotFound from '@/pages/NotFound';
import Login from '@/pages/Login';
import { ProtectedRoute } from '@/auth/ProtectedRoute';

export const routes: RouteObject[] = [
  { path: '/login', element: <Login /> },
  {
    path: '/',
    element: (
      <ProtectedRoute>
        <AppShell />
      </ProtectedRoute>
    ),
    children: [
      { index: true, element: <ExecutiveOverview /> },
      { path: 'suppliers', element: <SupplierIntelligence /> },
      { path: 'suppliers/:id', element: <SupplierDetail /> },
      { path: 'risk', element: <RiskCommandCenter /> },
      { path: 'risk/:id', element: <RiskCaseDetail /> },
      { path: 'recommendations', element: <AiRecommendations /> },
      { path: 'approvals', element: <ApprovalCenter /> },
      { path: 'what-if', element: <WhatIfSimulator /> },
      { path: 'integrations', element: <IntegrationCenter /> },
      { path: 'audit', element: <AuditCenter /> },
      { path: 'settings', element: <TenantSettings /> },
      { path: '*', element: <NotFound /> },
    ],
  },
];

export const createAppRouter = () => createBrowserRouter(routes);
