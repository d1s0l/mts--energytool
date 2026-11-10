import HomePage from "@/pages/home/ChatPage";
import { AppLayout } from "../layouts/AppLayout";
import { Route, Routes } from "react-router";
import { DebugPage } from "@/pages/debug/DebugPage";


export function AppRouter() {
    return(
        <Routes>
            <Route element={<AppLayout/>}>
                <Route
                    path="/"
                    element={<HomePage/>}
                />
                <Route
                    path="/debug"
                    element={<DebugPage />}
                />    
            </Route>
        </Routes>
    )
}