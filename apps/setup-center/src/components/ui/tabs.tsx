import React, { createContext, useContext, useState } from 'react';

const TabsContext = createContext<{
    value: string;
    onValueChange: (value: string) => void;
} | null>(null);

export const Tabs = ({ 
    defaultValue, 
    value: controlledValue, 
    onValueChange,
    children,
    className = '' 
}: { 
    defaultValue?: string;
    value?: string;
    onValueChange?: (value: string) => void;
    children: React.ReactNode;
    className?: string;
}) => {
    const [uncontrolledValue, setUncontrolledValue] = useState(defaultValue || '');
    const isControlled = controlledValue !== undefined;
    const currentValue = isControlled ? controlledValue : uncontrolledValue;
    
    const handleValueChange = (newValue: string) => {
        if (!isControlled) {
            setUncontrolledValue(newValue);
        }
        onValueChange?.(newValue);
    };
    
    return (
        <TabsContext.Provider value={{ value: currentValue, onValueChange: handleValueChange }}>
            <div className={className}>{children}</div>
        </TabsContext.Provider>
    );
};

export const TabsList = ({ children, className = '' }: { children: React.ReactNode; className?: string }) => {
    return <div className={`flex space-x-2 border-b ${className}`}>{children}</div>;
};

export const TabsTrigger = ({ 
    value, 
    children, 
    className = '' 
}: { 
    value: string; 
    children: React.ReactNode; 
    className?: string;
}) => {
    const context = useContext(TabsContext);
    const isActive = context?.value === value;
    
    return (
        <button
            className={`px-4 py-2 text-sm font-medium transition-colors ${className} ${isActive ? 'text-primary border-b-2 border-primary' : 'text-muted-foreground hover:text-primary'}`}
            onClick={() => context?.onValueChange(value)}
        >
            {children}
        </button>
    );
};

export const TabsContent = ({ 
    value, 
    children, 
    className = '' 
}: { 
    value: string; 
    children: React.ReactNode; 
    className?: string;
}) => {
    const context = useContext(TabsContext);
    if (context?.value !== value) return null;
    return <div className={className}>{children}</div>;
};
