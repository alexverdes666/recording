import { useEffect, useState } from 'react';
import './App.css';

interface Rule {
  type: 'domain' | 'application';
  value: string;
}

interface RulesState {
  domain: string[];
  application: string[];
}

function App() {
  const [rules, setRules] = useState<RulesState>({ domain: [], application: [] });
  const [newRuleType, setNewRuleType] = useState<'domain' | 'application'>('domain');
  const [newRuleValue, setNewRuleValue] = useState('');
  const [error, setError] = useState<string | null>(null);

  const API_URL = 'http://localhost:8000';

  const fetchRules = async () => {
    try {
      const response = await fetch(`${API_URL}/rules`);
      if (!response.ok) throw new Error('Failed to fetch rules');
      const data = await response.json();
      setRules(data);
    } catch (err) {
      setError('Could not connect to backend.');
      console.error(err);
    }
  };

  useEffect(() => {
    fetchRules();
  }, []);

  const handleAddRule = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newRuleValue.trim()) return;

    try {
      const response = await fetch(`${API_URL}/rules`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: newRuleType, value: newRuleValue.trim() }),
      });
      if (!response.ok) throw new Error('Failed to add rule');
      setNewRuleValue('');
      fetchRules();
    } catch (err) {
      setError('Failed to add rule.');
    }
  };

  const handleDeleteRule = async (type: 'domain' | 'application', value: string) => {
    try {
      const response = await fetch(`${API_URL}/rules`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, value }),
      });
      if (!response.ok) throw new Error('Failed to delete rule');
      fetchRules();
    } catch (err) {
      setError('Failed to delete rule.');
    }
  };

  return (
    <div className="container">
      <h1>Security & Monitoring Dashboard</h1>
      
      {error && <div className="error">{error}</div>}

      <div className="card">
        <h2>Add Restriction Rule</h2>
        <form onSubmit={handleAddRule} className="add-form">
          <select 
            value={newRuleType} 
            onChange={(e) => setNewRuleType(e.target.value as 'domain' | 'application')}
          >
            <option value="domain">Block Website (Domain)</option>
            <option value="application">Block Application (Process Name)</option>
          </select>
          <input 
            type="text" 
            placeholder={newRuleType === 'domain' ? "e.g., facebook.com" : "e.g., steam.exe"}
            value={newRuleValue}
            onChange={(e) => setNewRuleValue(e.target.value)}
          />
          <button type="submit">Add Block</button>
        </form>
      </div>

      <div className="grid">
        <div className="card">
          <h3>Blocked Websites</h3>
          {rules.domain.length === 0 ? (
            <p>No websites blocked.</p>
          ) : (
            <ul>
              {rules.domain.map((domain) => (
                <li key={domain}>
                  {domain}
                  <button onClick={() => handleDeleteRule('domain', domain)}>Remove</button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="card">
          <h3>Blocked Applications</h3>
          {rules.application.length === 0 ? (
            <p>No applications blocked.</p>
          ) : (
            <ul>
              {rules.application.map((app) => (
                <li key={app}>
                  {app}
                  <button onClick={() => handleDeleteRule('application', app)}>Remove</button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

export default App;

