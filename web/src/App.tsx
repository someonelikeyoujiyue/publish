import { Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Home } from './pages/Home'
import { UserNew, UserEdit } from './pages/UserForm'
import { DraftList } from './pages/DraftList'
import { DraftDetail } from './pages/DraftDetail'
import { Toutiao } from './pages/Toutiao'
import { Admin } from './pages/Admin'

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Home />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="/users/new" element={<UserNew />} />
        <Route path="/users/:userId/edit" element={<UserEdit />} />
        <Route path="/:userId/wechat" element={<DraftList platform="wechat" />} />
        <Route path="/:userId/wechat/:draftId" element={<DraftDetail platform="wechat" />} />
        <Route path="/:userId/xhs" element={<DraftList platform="xhs" />} />
        <Route path="/:userId/xhs/:draftId" element={<DraftDetail platform="xhs" />} />
        <Route path="/:userId/toutiao" element={<Toutiao />} />
      </Route>
    </Routes>
  )
}
