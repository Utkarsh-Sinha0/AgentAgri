import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

const RouterContext = createContext(null);

function currentLocation() {
  return {
    pathname: window.location.pathname,
    search: window.location.search,
    hash: window.location.hash,
  };
}

export function BrowserRouter({ children }) {
  const [location, setLocation] = useState(currentLocation);

  useEffect(() => {
    const onPopState = () => setLocation(currentLocation());
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const navigate = useCallback((to) => {
    const target = typeof to === 'string' ? to : to.pathname || '/';
    if (target !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history.pushState({}, '', target);
      setLocation(currentLocation());
    }
  }, []);

  const value = useMemo(() => ({ location, navigate }), [location, navigate]);
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

export function useLocation() {
  const ctx = useContext(RouterContext);
  if (!ctx) throw new Error('useLocation must be used inside BrowserRouter');
  return ctx.location;
}

export function useNavigate() {
  const ctx = useContext(RouterContext);
  if (!ctx) throw new Error('useNavigate must be used inside BrowserRouter');
  return ctx.navigate;
}

export function Link({ to, onClick, ...props }) {
  const navigate = useNavigate();
  return (
    <a
      href={to}
      onClick={(event) => {
        onClick?.(event);
        if (
          !event.defaultPrevented
          && event.button === 0
          && !event.metaKey
          && !event.altKey
          && !event.ctrlKey
          && !event.shiftKey
        ) {
          event.preventDefault();
          navigate(to);
        }
      }}
      {...props}
    />
  );
}

export function NavLink({ to, className, ...props }) {
  const { pathname } = useLocation();
  const active = pathname === to || (to !== '/' && pathname.startsWith(to));
  const resolvedClassName = typeof className === 'function'
    ? className({ isActive: active })
    : [className, active ? 'active' : ''].filter(Boolean).join(' ');
  return <Link to={to} className={resolvedClassName} {...props} />;
}

export function Route() {
  return null;
}

export function Routes({ children }) {
  const { pathname } = useLocation();
  const routes = React.Children.toArray(children).filter(Boolean);
  const fallback = routes.find((route) => route.props.path === '*');
  const match = routes.find((route) => route.props.path === pathname)
    || routes.find((route) => route.props.path !== '*' && route.props.path?.endsWith('/*') && pathname.startsWith(route.props.path.slice(0, -2)))
    || fallback;
  return match ? match.props.element : null;
}
