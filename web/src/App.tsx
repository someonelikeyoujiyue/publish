import { Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { ProtectedRoute } from './components/ProtectedRoute'
import { Login } from './pages/Login'
import { Home } from './pages/Home'
import { UserNew, UserEdit } from './pages/UserForm'
import { DraftList } from './pages/DraftList'
import { DraftDetail } from './pages/DraftDetail'
import { Toutiao } from './pages/Toutiao'
import { Admin } from './pages/Admin'

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<ProtectedRoute><Layout /></ProtectedRoute>}>
        <Route path="/" element={<Home />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="/users/new" element={<UserNew />} />
        <Route path="/users/:userId/edit" element={<UserEdit />} />
        <Route path="/:userId/wechat" element={<DraftList platform="wechat" />} />
        <Route path="/:userId/wechat/:draftId" element={<DraftDetail platform="wechat" />} />
        <Route path="/:userId/xhs" element={<DraftList platform="xhs" />} />
        <Route path="/:userId/xhs/:draftId" element={<DraftDetail platform="xhs" />} />
        <Route path="/:userId/toutiao" element={<Toutiao />} />
        <Route path="/:userId/toutiao/:draftId" element={<DraftDetail platform="toutiao" />} />
        <Route path="/:userId/douyin" element={<DraftList platform="douyin" />} />
        <Route path="/:userId/douyin/:draftId" element={<DraftDetail platform="douyin" />} />
      </Route>
    </Routes>
  )
}
