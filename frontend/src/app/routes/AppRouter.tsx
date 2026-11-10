import HomePage from "@/pages/home/HomePage";
import { AppLayout } from "../layouts/AppLayout";
import { Route, Routes } from "react-router";


export function AppRouter() {
    return(
        <Routes>
            <Route element={<AppLayout/>}>
                <Route
                    path="/"
                    element={<HomePage/>}
                />
            </Route>
        </Routes>
    )
}