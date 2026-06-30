import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

const DailyReviewPage = lazy(() => import("./pages/DailyReviewPage").then((module) => ({ default: module.DailyReviewPage })));
const CdkAdminPage = lazy(() => import("./pages/CdkAdminPage").then((module) => ({ default: module.CdkAdminPage })));
const CdkRecordsPage = lazy(() => import("./pages/CdkRecordsPage").then((module) => ({ default: module.CdkRecordsPage })));
const LiteratureSearchPage = lazy(() => import("./pages/LiteratureSearchPage").then((module) => ({ default: module.LiteratureSearchPage })));
const ReviewAdminPage = lazy(() => import("./pages/ReviewAdminPage").then((module) => ({ default: module.ReviewAdminPage })));
const WechatAdminPage = lazy(() => import("./pages/WechatAdminPage").then((module) => ({ default: module.WechatAdminPage })));

export function AppRoutes() {
  return (
    <Suspense fallback={<main className="daily-page standalone"><div className="daily-loading">正在加载页面资源...</div></main>}>
      <Routes>
        <Route path="/" element={<Navigate to="/daily-review" replace />} />
        <Route path="/daily-review" element={<DailyReviewPage />} />
        <Route path="/daily-review/:topicSlug/:runId" element={<DailyReviewPage />} />
        <Route path="/daily-review/:topicSlug" element={<DailyReviewPage />} />
        <Route path="/literature-search" element={<LiteratureSearchPage />} />
        <Route path="/literature-search/:searchId" element={<LiteratureSearchPage />} />
        <Route path="/admin" element={<ReviewAdminPage />} />
        <Route path="/admin/CDK" element={<CdkAdminPage />} />
        <Route path="/admin/CDK/records" element={<CdkRecordsPage />} />
        <Route path="/admin/cdk" element={<CdkAdminPage />} />
        <Route path="/admin/cdk/records" element={<CdkRecordsPage />} />
        <Route path="/admin/exclusive-review" element={<DailyReviewPage exclusive />} />
        <Route path="/admin/exclusive-review/:topicSlug/:runId" element={<DailyReviewPage exclusive />} />
        <Route path="/admin/exclusive-review/:topicSlug" element={<DailyReviewPage exclusive />} />
        <Route path="/admin/wechat" element={<WechatAdminPage />} />
        <Route path="*" element={<Navigate to="/daily-review" replace />} />
      </Routes>
    </Suspense>
  );
}
