import * as React from 'react';
type Variant = 'default' | 'quiet' | 'outline';
type Props = React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant };
/** Local, shadcn-style source-owned button, not an installed shadcn registry item. */
export const Button = React.forwardRef<HTMLButtonElement, Props>(function Button({ variant='default', className='', ...rest }, ref) {
  return <button ref={ref} className={`bn-button bn-button-${variant} ${className}`} {...rest}/>;
});