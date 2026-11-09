import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type AnchorHTMLAttributes,
  type ReactNode,
} from "react";

type RouterValue = {
  base: string;
  search: string;
  path: string;
  navigate: (to: string, options?: { replace?: boolean }) => void;
};

import { integrated, embedded, servicePath, openUnified, unifiedRoute } from './integration';

const RouterContext = createContext<RouterValue | null>(null);

function currentPath(base = "") {
  const path = window.location.pathname || "/";
  return base && (path === base || path.startsWith(`${base}/`))
    ? path.slice(base.length) || "/"
    : path;
}

export function Router({
  children,
  base = integrated ? "/registration" : "",
}: {
  children: ReactNode;
  base?: string;
}) {
  if (integrated && !base.startsWith("/registration")) base = servicePath(base);
  const [path, setPath] = useState(() => currentPath(base));
  const [search, setSearch] = useState(() => window.location.search);
  useEffect(() => {
    const onPopState = () => {
      setPath(currentPath(base));
      setSearch(window.location.search);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [base]);
  const value = useMemo<RouterValue>(
    () => ({
      base,
      path,
      search,
      navigate(to, options = {}) {
        if(integrated&&base==='/registration') {
          const page=to.split('?')[0],shared=unifiedRoute(page);
          if(shared){openUnified(shared);return}
          if(embedded&&['/register','/accounts','/orders'].includes(page)&&page!==path){openUnified(page);return}
        }
        const target = `${base}${to}`;
        if (options.replace) window.history.replaceState(null, "", target);
        else window.history.pushState(null, "", target);
        setPath(currentPath(base));
        setSearch(window.location.search);
        window.scrollTo({ top: 0, behavior: "instant" });
      },
    }),
    [path, search, base],
  );
  return (
    <RouterContext.Provider value={value}>{children}</RouterContext.Provider>
  );
}

export function useRouter() {
  const value = useContext(RouterContext);
  if (!value) throw new Error("Router context is missing");
  return value;
}

export function useNavigate() {
  return useRouter().navigate;
}

export function useParams() {
  const { path } = useRouter();
  const match = path.match(/^\/run\/([^/]+)$/);
  return { runId: match ? decodeURIComponent(match[1]) : undefined };
}

export function Link({
  to,
  onClick,
  children,
  ...props
}: Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & { to: string }) {
  const { navigate, base } = useRouter();
  return (
    <a
      href={`${base}${to}`}
      {...props}
      onClick={(event) => {
        onClick?.(event);
        if (
          event.defaultPrevented ||
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey
        )
          return;
        event.preventDefault();
        navigate(to);
      }}
    >
      {children}
    </a>
  );
}

export function NavLink({
  to,
  end = false,
  className,
  children,
  ...props
}: Omit<Parameters<typeof Link>[0], "className"> & {
  end?: boolean;
  className?: string | ((state: { isActive: boolean }) => string);
}) {
  const { path } = useRouter();
  const isActive = end ? path === to : path === to || path.startsWith(`${to}/`);
  const resolved =
    typeof className === "function" ? className({ isActive }) : className;
  return (
    <Link to={to} className={resolved} {...props}>
      {children}
    </Link>
  );
}

export function NativeRouter({children,path,navigate}:{children:ReactNode;path:string;navigate:RouterValue["navigate"]}){return <RouterContext.Provider value={{base:"",path,search:"",navigate}}>{children}</RouterContext.Provider>;}
