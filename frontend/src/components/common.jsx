import { Film } from 'lucide-react';

export function Button({ children, icon: Icon, primary = false, small = false, ...props }) {
  return (
    <button className={`${primary ? 'primary' : ''} ${small ? 'small' : ''}`} {...props}>
      {Icon && <Icon size={small ? 14 : 16} />} {children}
    </button>
  );
}

export function Field({ label, children, hint }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

export function NumberField({
  label,
  value,
  onChange,
  min = 0,
  max = 100,
  step = 0.1,
  disabled = false,
}) {
  return (
    <Field label={label}>
      <input
        type="number"
        disabled={disabled}
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </Field>
  );
}

export function Empty({ icon: Icon = Film, title, children }) {
  return (
    <div className="empty">
      <Icon size={34} />
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
