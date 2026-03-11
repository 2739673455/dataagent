import { Link } from "react-router-dom";
import { Compass } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function NotFound() {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <Card className="max-w-lg">
        <CardHeader>
          <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-accent/15 text-accent">
            <Compass className="h-7 w-7" />
          </div>
          <CardTitle>页面不存在</CardTitle>
          <CardDescription>
            当前路径没有对应的页面入口，返回聊天主页继续工作
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button asChild>
            <Link to="/chat">返回聊天页</Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
