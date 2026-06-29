import { Navigate, Route, Routes } from "react-router-dom";

import { DailyReviewPage } from "./pages/DailyReviewPage";
import { CdkAdminPage } from "./pages/CdkAdminPage";
import { LiteratureSearchPage } from "./pages/LiteratureSearchPage";
import { ReviewAdminPage } from "./pages/ReviewAdminPage";
import { WechatAdminPage } from "./pages/WechatAdminPage";

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/daily-review" replace />} />
      <Route path="/daily-review" element={<DailyReviewPage />} />
      <Route path="/daily-review/:topicSlug/:runId" element={<DailyReviewPage />} />
      <Route path="/daily-review/:topicSlug" element={<DailyReviewPage />} />
      <Route path="/literature-search" element={<LiteratureSearchPage />} />
      <Route path="/literature-search/:searchId" element={<LiteratureSearchPage />} />
      <Route path="/admin" element={<ReviewAdminPage />} />
      <Route path="/admin/CDK" element={<CdkAdminPage />} />
      <Route path="/admin/exclusive-review" element={<DailyReviewPage exclusive />} />
      <Route path="/admin/exclusive-review/:topicSlug/:runId" element={<DailyReviewPage exclusive />} />
      <Route path="/admin/exclusive-review/:topicSlug" element={<DailyReviewPage exclusive />} />
      <Route path="/admin/wechat" element={<WechatAdminPage />} />
      <Route path="*" element={<Navigate to="/daily-review" replace />} />
    </Routes>
  );
}
