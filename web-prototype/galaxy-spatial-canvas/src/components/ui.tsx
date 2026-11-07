import * as React from 'react';
export type ButtonVariant = 'primary'|'quiet'|'outline';
export const Button=React.forwardRef<HTMLButtonElement,React.ButtonHTMLAttributes<HTMLButtonElement>&{variant?:ButtonVariant}> (
  function Button({variant='quiet',className='',...props},ref){return <button ref={ref} className={`ui-button ui-button--${variant} ${className}`} {...props}/>}
);